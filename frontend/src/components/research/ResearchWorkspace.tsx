'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Input, Tabs, cn } from '@cloudflare/kumo';
import { ArrowClockwiseIcon, ChatCircleIcon, GraphIcon, PlusIcon, SidebarSimpleIcon, TrashIcon } from '@phosphor-icons/react';
import * as api from '../../lib/research/api';
import { labSample, loadLabSample } from '../../lib/research/demo';
import { allObligations, chainHighlight, filterGraph, humanize, projectWorkspace } from '../../lib/research/graph';
import type { AgentRun, GraphQuestion, Snapshot, Verification } from '../../types/api';
import { ResearchExperiments, ResearchStages } from './ResearchExperiments';
import { ResearchCanvas } from './ResearchCanvas';
import { ResearchVerificationProgress } from './ResearchVerificationProgress';
import type { VerificationTrace } from '../../lib/research/verification';
import { ResearchInspector } from './ResearchInspector';
import { ArgumentChain, MaterialPanel } from './ResearchPanels';
import type { ResearchComposerMode, ResearchMessage } from './ResearchUpload';
import { ResearchChat, isSidebarOpen, withSidebar, type ChatLayout } from './ResearchChat';
import { isAgentActive, useAgentRuns } from '../../lib/research/agent';
import { panel, panelSection } from '../ui/classes';
const row = 'h-auto! min-h-8 w-full justify-start! px-2! py-2! text-left text-[11px]! font-normal! whitespace-normal!';
export function ResearchWorkspace() {
  const client = useQueryClient();
  const [verificationTrace, setVerificationTrace] = useState<VerificationTrace>();
  const verificationController = useRef<AbortController | null>(null);
  const [chatLayout, setChatLayout] = useState<ChatLayout>({ dock: 'centre', open: true, details: false });
  const sidebarOpen = isSidebarOpen(chatLayout);
  const chatDocked = chatLayout.dock === 'side';
  const [composerMode, setComposerMode] = useState<ResearchComposerMode>('material');
  const [uploadProgress, setUploadProgress] = useState<api.UploadProgress>();
  const [id, setId] = useState<string>(); const [selected, setSelected] = useState<string>(); const [target, setTarget] = useState<string>(); const [tab, setTab] = useState('argument');
  useEffect(() => () => verificationController.current?.abort(), [id]);
  const [collapsed, setCollapsed] = useState(false); const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false); const [deleting, setDeleting] = useState(false);
  const [query, setQuery] = useState(''); const [lifecycle, setLifecycle] = useState(''); const [source, setSource] = useState(''); const [detailedGraph, setDetailedGraph] = useState(false); const [verification, setVerification] = useState<Record<string, Verification>>({});
  const [questions, setQuestions] = useState<GraphQuestion[]>([]);
  const [highlight, setHighlight] = useState<{ ids: string[]; label: string }>();
  const highlighted = useMemo(() => new Set(highlight?.ids ?? []), [highlight]);
  const workspaces = useQuery({ queryKey: ['workspaces'], queryFn: api.listWorkspaces, refetchInterval: 10000, retry: 1 });
  const current = useQuery({ queryKey: ['snapshot', id], queryFn: () => api.fetchSnapshot(id!), enabled: !!id, refetchInterval: 3000, retry: 1 });
  const experiments = useQuery({ queryKey: ['experiments', id], queryFn: () => api.fetchExperiments(id!), enabled: !!id, refetchInterval: 2000, retry: 1 });
  const agentRuns = useAgentRuns(id);
  const health = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15000, retry: false });
  const state = current.data;
  const graph = useMemo(() => state ? filterGraph(projectWorkspace(state, detailedGraph, highlighted), query, lifecycle, source) : { nodes: [], edges: [] }, [state, detailedGraph, query, lifecycle, source, highlighted]);
  function showChain(ids: string[], label: string) { setHighlight({ ids, label }); setSelected(undefined); setQuery(''); setLifecycle(''); setSource(''); chooseTab('argument'); }
  useEffect(() => {
    function sync() { const saved = new URLSearchParams(window.location.search).get('workspace'); setId(saved || undefined); setSelected(undefined); setTarget(undefined); const view = new URLSearchParams(window.location.search).get('view'); setTab(view && ['argument', 'material', 'chain', 'checks', 'experiments'].includes(view) ? view : 'argument'); }
    sync(); window.addEventListener('popstate', sync); return () => window.removeEventListener('popstate', sync);
  }, []);
  function chooseTab(view: string) { setTab(view); const url = new URL(window.location.href); url.searchParams.set('view', view); window.history.replaceState(null, '', url); }
  function load(workspace?: string, material = false) {
    setUploadProgress(undefined); setHighlight(undefined); setId(workspace); setSelected(undefined); setTarget(undefined); chooseTab(material ? 'material' : 'argument'); setError(''); setNotice(''); setQuery(''); setLifecycle(''); setSource(''); setDetailedGraph(false); setDeleting(false);
    const url = new URL(window.location.href); if (workspace) url.searchParams.set('workspace', workspace); else url.searchParams.delete('workspace'); window.history.pushState(null, '', url);
  }
  async function refresh() { await Promise.all([client.invalidateQueries({ queryKey: ['snapshot'] }), client.invalidateQueries({ queryKey: ['workspaces'] }), client.invalidateQueries({ queryKey: ['health'] }), client.invalidateQueries({ queryKey: ['agent-runs'] }), client.invalidateQueries({ queryKey: ['experiments'] })]); }
  async function perform(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { await refresh(); setBusy(false); }
  }
  async function sendMessage(message: ResearchMessage) {
    if (message.mode === 'ask') return askGraph(message.prompt);
    setBusy(true); setError(''); setNotice('');
    try {
      let workspaceId = id;
      const question = message.prompt || 'Read the attached documents and map their main claims, evidence, and reasoning into the argument graph.';
      if (!workspaceId) {
        const title = message.prompt || message.files[0]?.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ') || 'New research';
        const workspace = await api.createWorkspace(title.slice(0, 255));
        workspaceId = workspace.id;
        load(workspaceId);
      }
      chooseTab('argument'); setSelected(undefined);
      if (message.mode === 'material') {
        const files = [...message.files];
        if (message.prompt) files.unshift(new File([message.prompt], 'pasted-research.md', { type: 'text/markdown' }));
        await api.uploadResearch(workspaceId, files, true, setUploadProgress);
        return;
      }
      if (message.files.length) await api.uploadResearch(workspaceId, message.files, false, setUploadProgress);
      const run = await api.startAgentRun(workspaceId, { ...message.options, question });
      client.setQueryData<AgentRun[]>(['agent-runs', workspaceId], previous => [run, ...(previous ?? []).filter(item => item.id !== run.id)]);
    } finally { await refresh(); setBusy(false); }
  }
  async function askGraph(question: string) {
    if (!id) throw new Error('Open a workspace with a graph first.');
    const workspaceId = id;
    const entry: GraphQuestion = { id: crypto.randomUUID(), workspaceId, question, createdAt: new Date().toISOString(), status: 'pending' };
    const history = questions.filter(q => q.workspaceId === workspaceId && q.answer).slice(-6).map(q => ({ question: q.question, answer: q.answer!.answer }));
    setQuestions(previous => [...previous, entry]);
    setBusy(true);
    try {
      const answer = await api.askGraph(workspaceId, question, history);
      setQuestions(previous => previous.map(q => q.id === entry.id ? { ...q, status: 'answered', answer } : q));
      const snapshot = client.getQueryData<Snapshot>(['snapshot', workspaceId]);
      const ids = snapshot ? chainHighlight(snapshot, answer.statement_ids, answer.step_ids) : [...answer.statement_ids, ...answer.step_ids];
      if (ids.length) showChain(ids, question);
    } catch (e) {
      setQuestions(previous => previous.map(q => q.id === entry.id ? { ...q, status: 'failed', error: e instanceof Error ? e.message : String(e) } : q));
    } finally { setBusy(false); }
  }
  function stopResearch(run: AgentRun) {
    void perform(async () => {
      const cancelled = await api.cancelAgentRun(run.id);
      client.setQueryData<AgentRun[]>(['agent-runs', run.workspace_id], previous => previous?.map(item => item.id === run.id ? cancelled : item));
    });
  }
  function show(node: string) { setSelected(node); if (state?.graph.statements.some(s => s.id === node)) setTarget(node); chooseTab('argument'); }
  async function runVerification(workspace: string) {
    verificationController.current?.abort();
    const controller = new AbortController(); verificationController.current = controller;
    setVerificationTrace({ workspaceId: workspace, status: 'running', events: [] });
    setSelected(undefined); setQuery(''); setLifecycle(''); setSource(''); chooseTab('checks');
    try {
      const result = await api.verifyLive(workspace, async event => {
        setVerificationTrace(previous => previous?.workspaceId === workspace ? { ...previous, events: [...previous.events, event] } : previous);
        // Give each real rule event time to be read and its graph transition to be seen.
        if (event.type === 'rule_started' && !window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches) await new Promise(resolve => window.setTimeout(resolve, 500));
      }, controller.signal);
      setVerification(v => ({ ...v, [workspace]: result }));
      setVerificationTrace(previous => previous?.workspaceId === workspace ? { ...previous, status: 'complete' } : previous);
      await refresh();
      setNotice(`Verification completed: ${result.issues_opened} issues opened, ${result.issues_resolved} resolved; ${result.obligations_opened} obligations opened, ${result.obligations_resolved} resolved.`);
    } catch (error) {
      if (controller.signal.aborted) return;
      setVerificationTrace(previous => previous?.workspaceId === workspace ? { ...previous, status: 'failed' } : previous);
      throw error;
    }
  }
  const lastVerification = id ? verification[id] ?? experiments.data?.verification ?? undefined : undefined;
  const knownObligations = state ? allObligations(state).filter(o => o.status === 'open').length : 0;
  const targets = state?.graph.statements.filter(s => s.role === 'conclusion') ?? [];
  const activeJobs = state?.jobs.filter(j => !['succeeded', 'failed', 'cancelled'].includes(j.status)) ?? [];
  const activeResearch = agentRuns.data?.filter(isAgentActive) ?? [];
  const processingResearch = activeJobs.length > 0 || busy && !!uploadProgress || uploadProgress?.stage === 'queued' && !state?.jobs.length;
  const workflowStage = tab === 'experiments' ? 2 : tab === 'checks' ? 1 : 0;
  function changeStage(stage: number) { setSelected(undefined); chooseTab(stage === 2 ? 'experiments' : stage === 1 ? 'checks' : 'argument'); }
  return <main className="research-workspace flex h-dvh min-h-[560px] overflow-hidden text-xs"><aside aria-label="Research navigation" className={cn('flex shrink-0 flex-col border-r border-line bg-zinc-100 p-2', collapsed ? 'w-[58px]' : 'w-[208px] max-[700px]:w-[58px]')}>
    <div className="flex h-11 items-center justify-between px-1"><button disabled={busy} className={cn('text-[13px] font-semibold', collapsed && 'hidden', 'max-[700px]:hidden')} onClick={() => load()}>Trial</button><Button size="sm" variant="ghost" shape="square" aria-label="Toggle research navigation" icon={<SidebarSimpleIcon size={17}/>} onClick={() => setCollapsed(!collapsed)}/></div>
    <Button size="sm" variant="outline" disabled={busy} className="my-3 text-[11px]!" icon={<PlusIcon size={14}/>} aria-label="New research workspace" onClick={() => load()}><span className={cn(collapsed && 'hidden', 'max-[700px]:hidden')}>New research</span></Button>
    {!collapsed && <section aria-label="Demo sample" className="mb-3 border-b border-line px-2 py-3 max-[700px]:hidden"><p className="mb-2 text-[9px] text-zinc-400">Demo sample</p><button disabled={busy} className="w-full text-left text-[11px] leading-5 hover:text-zinc-500 disabled:opacity-50" onClick={() => perform(async () => { const existing = workspaces.data?.find(w => w.title === labSample.title); if (existing) load(existing.id); else await loadLabSample(workspace => load(workspace)); setNotice('DJI_08 is ready. Verify the research, then run the saved sample analysis.'); })}><strong className="block font-medium">DJI_08 · 30 seconds</strong><span className="text-[10px] text-zinc-400">Cell preparation · protocol + video</span></button><div className="mt-3 flex gap-3 text-[9px] text-zinc-500"><a href={labSample.protocol} download className="underline">Download protocol</a><a href={labSample.video} download className="underline">Download video</a></div></section>}
    {!collapsed && <div className="flex-1 overflow-y-auto max-[700px]:hidden"><p className="px-2 py-2 text-[10px] text-zinc-500">Research workspaces</p>{workspaces.data?.map(w => <Button variant="ghost" size="sm" disabled={busy} key={w.id} className={cn(row, id === w.id && 'bg-zinc-200!')} onClick={() => load(w.id)}><span className="line-clamp-2">{w.title}</span></Button>)}{!!targets.length && <><p className="mt-4 px-2 py-2 text-[10px] text-zinc-500">Target conclusions</p>{targets.map(s => <Button size="sm" variant="ghost" key={s.id} className={cn(row, target === s.id && 'bg-zinc-200!')} onClick={() => { setTarget(s.id); show(s.id); }}>{s.text}</Button>)}</>}</div>}
    <div className="mt-auto border-t border-line pt-3"><p className="px-2 text-[10px] text-zinc-500">{health.data?.status === 'ok' ? 'API connected' : health.isPending ? 'Connecting…' : 'API unavailable'}</p><a href="/docs" target="_blank" rel="noreferrer" className={cn('mt-3 block px-2 text-[10px] text-zinc-500 underline', collapsed && 'hidden', 'max-[700px]:hidden')}>API documentation</a></div>
  </aside><section className="research-main flex min-w-0 flex-1 flex-col"><header className="research-header min-h-[52px] shrink-0 border-b border-line bg-zinc-50"><div className="research-header-title flex min-w-0 items-center gap-3 text-[11px] max-[700px]:hidden"><span className="text-zinc-500 max-[700px]:hidden">Research</span><span className="text-zinc-300 max-[700px]:hidden">/</span><strong className="truncate font-medium">{state?.workspace.title ?? 'Add research'}</strong></div>
      <ResearchStages stage={workflowStage} hasResearch={!!state?.sources.length} verified={!!experiments.data?.verified} onChange={changeStage}/>
      <div className="research-header-actions flex min-w-0 flex-wrap items-center justify-end gap-2">{id && !['experiments', 'checks'].includes(tab) && <Button size="xs" variant="ghost" aria-label={`${sidebarOpen ? 'Hide' : 'Show'} ${chatDocked ? 'chat sidebar' : 'agent details'}`} aria-expanded={sidebarOpen} aria-controls="research-chat" icon={<ChatCircleIcon size={14}/>} onClick={() => setChatLayout(current => withSidebar(current, !isSidebarOpen(current)))}><span className="research-header-chat-label max-[700px]:hidden">{chatDocked ? sidebarOpen ? 'Hide chat' : 'Show chat' : sidebarOpen ? 'Hide details' : 'Agent details'}</span></Button>}{id && <select disabled={busy} aria-label="Switch workspace" className="max-w-32 rounded border border-line bg-transparent p-1 text-[10px] min-[701px]:hidden" value={id} onChange={e => load(e.target.value)}>{workspaces.data?.map(w => <option key={w.id} value={w.id}>{w.title}</option>)}</select>}{state && <><Button size="xs" variant={tab === 'material' ? 'outline' : 'ghost'} disabled={busy} aria-pressed={tab === 'material'} onClick={() => { chooseTab('material'); setSelected(undefined); }}>Material</Button><Button size="xs" variant="ghost" disabled={busy} onClick={() => api.downloadSnapshot({ ...state, experiments: experiments.data })}>Export</Button><Button size="xs" variant="ghost" shape="square" disabled={busy} aria-label="Refresh workspace" icon={<ArrowClockwiseIcon size={14}/>} onClick={() => perform(refresh)}/><Button size="xs" variant="ghost" shape="square" disabled={busy} aria-label="Delete workspace" icon={<TrashIcon size={14}/>} onClick={() => setDeleting(true)}/></>}</div></header>
    {id && <div className="flex min-h-[43px] shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line bg-zinc-50 px-4 py-1 [&_[role=tab]]:text-[11px]"><Tabs size="sm" variant="underline" value={tab} onValueChange={value => { chooseTab(value); setSelected(undefined); }} tabs={[{ value: 'argument', label: 'Argument' }, { value: 'chain', label: 'Chain' }, { value: 'checks', label: 'Verification' }, { value: 'experiments', label: 'Experiments' }]}/><div className="flex items-center gap-2">{!!activeResearch.length && <Button size="xs" variant="ghost" className="text-running!" onClick={() => setChatLayout(current => withSidebar(current, true))}><span className="research-processing-dot mr-2" aria-hidden/>Researching</Button>}{tab === 'argument' && <Button size="xs" variant="outline" aria-pressed={detailedGraph} onClick={() => setDetailedGraph(value => !value)}>{detailedGraph ? 'Overview' : 'All evidence'}</Button>}<span className="text-[10px] text-zinc-500 max-[1000px]:hidden">{knownObligations} evidence gaps{activeJobs.length ? ` · ${activeJobs.length} extraction jobs` : ''}</span></div></div>}
    {deleting && state && <div className="flex flex-wrap items-center justify-between gap-3 border-b border-red-200 bg-red-50 px-5 py-3 text-[11px]"><p>Delete “{state.workspace.title}” and all of its graph data? This cannot be undone.</p><div className="flex gap-2"><Button size="xs" disabled={busy} variant="ghost" onClick={() => setDeleting(false)}>Keep workspace</Button><Button size="xs" disabled={busy} variant="destructive" onClick={() => perform(async () => { await api.deleteWorkspace(state.workspace.id); client.removeQueries({ queryKey: ['snapshot', state.workspace.id] }); load(); })}>Delete permanently</Button></div></div>}
    <div className="relative grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_auto] grid-rows-[minmax(0,1fr)_auto]"><div className="relative col-start-1 row-start-1 min-h-0 min-w-0 overflow-hidden">
      {!id ? <div className="flex h-full flex-col overflow-y-auto px-6 py-6 sm:px-12"><div className="research-introduction mx-auto my-auto w-full max-w-2xl py-4">
        <a href="https://arxiv.org/abs/1512.03385" target="_blank" rel="noreferrer" className="research-paper-preview mb-7 block" aria-label="Preview Deep Residual Learning for Image Recognition"><img src="/images/research-paper-preview.png" width={774} height={1000} alt="First page of a research paper, with its title, abstract, and two charts" className="h-full w-full object-cover"/></a>
        <p className="mb-2 text-xs text-zinc-400">Scientific reasoning, made visible.</p>
        <h1 className="max-w-xl text-[25px] font-normal leading-[1.4] tracking-[-.025em] sm:text-[30px]">Is your agentic science correct?</h1>
        <p className="mt-4 max-w-xl text-sm leading-[1.8] text-zinc-500">Map claims to evidence. Expose assumptions and missing support, so scientific reasoning can be challenged and tested.</p>
        <div className="mt-7 flex flex-wrap items-center gap-3 text-xs text-zinc-400"><Button size="sm" variant="outline" className="rounded-sm! font-normal!" loading={busy} onClick={() => perform(async () => { await loadLabSample(workspace => load(workspace)); setNotice('DJI_08 protocol added. Verify the research, then run the 30-second sample.'); })}>Try the full demo</Button><span>Paste research text or attach your own paper below.</span></div>
        {!!workspaces.data?.length && <select aria-label="Open existing workspace" className="mt-5 w-full rounded-sm border border-line p-2 min-[701px]:hidden" defaultValue="" onChange={e => load(e.target.value)}><option value="" disabled>Open an existing workspace</option>{workspaces.data.map(w => <option key={w.id} value={w.id}>{w.title}</option>)}</select>}
      </div></div>
      : !state ? <div className="p-8 text-zinc-500"><p>{current.error?.message ?? 'Loading graph…'}</p><Button className="mt-3" size="sm" variant="outline" disabled={busy} onClick={() => perform(refresh)}>Retry connection</Button><Button className="ml-3 mt-3" size="sm" variant="ghost" onClick={() => load()}>Back to workspaces</Button></div>
      : <>{tab === 'argument' ? <><ResearchCanvas key={id} graph={processingResearch && !state.graph.statements.length ? { nodes: [], edges: [] } : graph} highlight={highlighted} selected={selected} onSelect={node => { setSelected(node); if (state.graph.statements.some(s => s.id === node)) setTarget(node); }}/><div className={cn('absolute top-4 left-4 z-5 flex max-w-[calc(100%-32px)] flex-wrap items-center gap-2 rounded-md border border-line bg-white/95 p-2', selected && 'min-[900px]:max-w-[calc(100%-380px)]')}><Input size="xs" aria-label="Search graph" placeholder="Search statements or identifiers" value={query} onChange={e => setQuery(e.target.value)}/><select aria-label="Graph lifecycle filter" className="rounded border border-line p-1 text-[10px]" value={lifecycle} onChange={e => setLifecycle(e.target.value)}><option value="">All review states</option><option value="proposed">Proposed</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option></select><select aria-label="Graph source filter" className="max-w-32 rounded border border-line p-1 text-[10px]" value={source} onChange={e => setSource(e.target.value)}><option value="">All sources</option>{state.sources.map(s => <option key={s.id} value={s.id}>{s.original_filename}</option>)}</select>{(query || lifecycle || source) && <Button size="xs" variant="ghost" onClick={() => { setQuery(''); setLifecycle(''); setSource(''); }}>Reset filters</Button>}</div>{(!graph.nodes.length || processingResearch && !state.graph.statements.length) && <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-8"><p className="max-w-sm text-center text-[12px] text-zinc-500">{query || lifecycle || source ? 'No matching graph objects. Reset filters to see the full argument.' : activeResearch.length ? 'The agent is researching. Claims and evidence will appear here as they are recorded.' : processingResearch ? 'Reading your research. Claims and their connections will appear here as they are extracted.' : 'Ask a question in chat or attach a paper to begin building the graph.'}</p></div>}<div className="absolute bottom-5 left-40 z-5 flex flex-wrap gap-2 max-[700px]:bottom-16 max-[700px]:left-5"><Button size="sm" loading={busy} disabled={!state.graph.statements.length} onClick={() => perform(() => runVerification(state.workspace.id))}>Verify research</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => { setComposerMode('material'); setChatLayout(current => ({ ...current, open: true })); setSelected(undefined); requestAnimationFrame(() => document.getElementById('research-prompt')?.focus()); }}>Add material</Button></div>{tab === 'argument' && highlight && <div role="status" aria-label="Highlighted reasoning" className="absolute bottom-5 left-1/2 z-5 flex max-w-[min(560px,calc(100%-180px))] -translate-x-1/2 items-center gap-3 rounded-full border border-blue-200 bg-white/95 px-4 py-2 text-[11px] shadow-sm"><GraphIcon size={14} className="shrink-0 text-blue-600"/><span className="truncate">Highlighting the reasoning for “{highlight.label}”</span><Button size="xs" variant="ghost" onClick={() => setHighlight(undefined)}>Clear</Button></div>}</>
      : tab === 'material' ? <MaterialPanel key={id} state={state} busy={busy} perform={perform} refresh={refresh} onSelect={show} onArgument={() => { chooseTab('argument'); setSelected(undefined); }}/>
      : tab === 'chain' ? <ArgumentChain state={state} target={target ?? targets[0]?.id ?? state.graph.statements[0]?.id} onSelect={show}/>
      : tab === 'experiments' ? <ResearchExperiments key={id} state={state} data={experiments.data} loading={experiments.isPending} error={experiments.error} busy={busy} perform={perform} onVerify={() => changeStage(1)} onSource={show}/>
      : <div className="flex h-full min-h-0 flex-col"><div className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-white px-6 py-3"><p className="text-[11px] text-zinc-500">{experiments.data?.verified ? 'Research checks are current. Review any evidence gaps before proceeding.' : 'Check the claims, reasoning, and evidence before preparing an experiment.'}</p><Button size="sm" variant="outline" disabled={busy || !experiments.data?.verified || processingResearch} onClick={() => changeStage(2)}>Continue to experiment</Button></div><div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px] max-[1000px]:grid-cols-[minmax(0,1fr)_300px] max-[800px]:grid-cols-1 max-[800px]:grid-rows-[minmax(260px,1fr)_minmax(200px,1fr)]"><div className="relative min-h-0 min-w-0 overflow-hidden border-r border-line"><ResearchCanvas graph={graph} onSelect={node => setSelected(node)} verification={verificationTrace?.workspaceId === id ? verificationTrace : undefined}/><ResearchVerificationProgress trace={verificationTrace?.workspaceId === id ? verificationTrace : undefined}/></div><ChecksPanel compact key={id} state={state} busy={busy} canVerify={!processingResearch && !activeResearch.length} lastVerification={lastVerification} perform={perform} refresh={refresh} onVerify={() => runVerification(state.workspace.id)} onSelect={show} onNotice={setNotice}/></div></div>}
      {selected && <ResearchInspector key={selected} state={state} id={selected} busy={busy} perform={perform} refresh={refresh} onClose={() => setSelected(undefined)} onSelect={show}/>}</>}
    </div>
    <ResearchChat mode={composerMode} onModeChange={setComposerMode} workspaceId={id} runs={agentRuns.data ?? []} questions={questions.filter(q => q.workspaceId === id)} onHighlight={showChain} loading={!!id && agentRuns.isPending} error={agentRuns.error} busy={busy} layout={chatLayout} suspended={['experiments', 'checks'].includes(tab)} onLayout={setChatLayout} state={state} progress={uploadProgress} onSubmit={sendMessage} onStop={stopResearch} onSelect={show} onRetry={() => void agentRuns.refetch()} onCancelExtraction={() => void perform(async () => { await Promise.all(activeJobs.map(job => api.cancelJob(job.id))); })}/>
    </div>
    {(error || workspaces.error || current.error && state) && <div role="alert" className="flex shrink-0 items-center justify-between gap-3 border-t border-red-200 bg-red-50 px-5 py-3 text-[11px] text-fail"><span className="break-words">{error || workspaces.error?.message || current.error?.message}</span><Button size="xs" variant="ghost" onClick={() => { setError(''); void refresh(); }}>Retry / dismiss</Button></div>}
    {notice && <div role="status" className="flex shrink-0 items-center justify-between gap-3 border-t border-line bg-white px-5 py-3 text-[11px]"><span>{notice}</span><Button size="xs" variant="ghost" onClick={() => setNotice('')}>Dismiss</Button></div>}
  </section></main>;
}
function ChecksPanel({ compact = false, state, busy, canVerify, lastVerification, perform, refresh, onVerify, onSelect, onNotice }: { compact?: boolean; state: Snapshot; busy: boolean; canVerify: boolean; lastVerification?: Verification; perform: (action: () => Promise<void>) => Promise<void>; refresh: () => Promise<void>; onVerify: () => Promise<void>; onSelect: (id: string) => void; onNotice: (message: string) => void }) {
  const [critic, setCritic] = useState<{ checked_steps: number; flagged_steps: number }>();
  const [synthesis, setSynthesis] = useState<{ proposed_links: number; links_needing_review: number }>();
  const obligations = allObligations(state).filter(item => item.status === 'open');
  return <div className={compact ? 'h-full overflow-y-auto px-5 py-6' : panel}><div className="mx-auto max-w-4xl">
    <p className="mb-2 text-[10px] text-zinc-400">Claims → Evidence → Reasoning</p>
    <h1 className="text-2xl tracking-tight">Verify your research</h1>
    <p className="mt-3 max-w-2xl text-xs leading-6 text-zinc-500">Check that claims have evidence, conclusions follow from their premises, and causal statements have the support they need. Review the gaps before taking the methodology into the lab.</p>
    <section className="mt-7 border-t border-line py-6">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm">Research checks</h2><p className="mt-2 text-[11px] leading-5 text-zinc-500">Source grounding, missing premises, scope, conflicts, and causal claims.</p></div><Button size="sm" loading={busy} disabled={!canVerify || !state.graph.statements.length} onClick={() => perform(onVerify)}>{lastVerification ? 'Run checks again' : 'Verify research'}</Button></div>
      {!canVerify && <p className="mt-4 text-[11px] text-running">Research is still processing. Run verification when it finishes.</p>}
      {lastVerification && <div className="mt-5 grid grid-cols-3 gap-3">{[[lastVerification.rules_run.length, 'checks run'], [obligations.length, 'evidence gaps'], [lastVerification.issues_resolved, 'issues resolved']].map(([value, label]) => <div key={label} className={cn("py-2", label === "evidence gaps" ? obligations.length ? "text-warn" : "text-pass" : "text-running")}><p className="text-4xl">{value}</p><p className="mt-1 text-[10px] text-zinc-400">{label}</p></div>)}</div>}
      <p className="mt-4 text-[10px] leading-5 text-zinc-400">These checks identify gaps in the argument. An experiment can help investigate them; completing verification does not establish that a scientific claim is true.</p>
    </section>
    <section className="mt-5 border-t border-line py-6"><h2 className="text-sm">Evidence gaps to review</h2><p className="mt-2 text-[11px] leading-5 text-zinc-500">Open a finding to inspect the claim and its supporting evidence.</p>
      {obligations.map(obligation => <Button key={obligation.id} size="sm" variant="ghost" className="mt-3 h-auto! w-full justify-between! border-b border-line py-3! text-left text-[11px]! whitespace-normal!" onClick={() => onSelect(obligation.id)}><span>{obligation.description}</span><span className="ml-3 shrink-0 rounded-full bg-warn/10 px-2 py-1 text-[9px] text-warn">Needs review</span></Button>)}
      {!obligations.length && <p className="mt-4 text-[11px] text-zinc-400">{lastVerification ? 'No open evidence gaps were found by the current checks.' : 'Run verification to identify missing support and assumptions.'}</p>}
    </section>
    <details className={panelSection}><summary className="cursor-pointer text-xs text-zinc-500">Additional research tools</summary>
      <section className="mt-5"><h2 className="text-xs">Model review of reasoning</h2><p className="my-3 text-[11px] leading-5 text-zinc-500">Ask Claude whether the premise passages establish each conclusion. This uses paid model tokens.</p><Button size="sm" variant="outline" disabled={busy || !canVerify || !state.graph.reasoning_steps.length} onClick={() => perform(async () => { const result = await api.checkArguments(state.workspace.id); setCritic(result); await onVerify(); onNotice(`Reviewed ${result.checked_steps} reasoning steps; ${result.flagged_steps} need attention.`); })}>Review reasoning</Button>{critic && <p className="mt-3 text-[11px]">{critic.checked_steps} steps checked · {critic.flagged_steps} flagged</p>}</section>
      <section className="mt-5"><h2 className="text-xs">Connect evidence across sources</h2><p className="my-3 text-[11px] leading-5 text-zinc-500">Propose and review supporting or conflicting links between your papers. This uses paid model tokens.</p><Button size="sm" variant="outline" disabled={busy || !canVerify || !state.graph.statements.length} onClick={() => perform(async () => { const result = await api.synthesize(state.workspace.id); setSynthesis(result); await refresh(); onNotice(`${result.proposed_links} evidence links proposed; ${result.links_needing_review} need review. Run verification again to check the updated research.`); })}>Connect sources</Button>{synthesis && <p className="mt-3 text-[11px]">{synthesis.proposed_links} proposed links · {synthesis.links_needing_review} need review</p>}{state.graph.relations.map(relation => <Button key={relation.id} variant="ghost" size="sm" className="mt-3 h-auto! w-full justify-start! text-left whitespace-normal!" onClick={() => onSelect(relation.id)}>{humanize(relation.relation)} · Inspect link</Button>)}</section>
    </details>
  </div></div>;
}
