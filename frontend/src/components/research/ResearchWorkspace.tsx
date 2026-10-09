'use client';
import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Input, Tabs, cn } from '@cloudflare/kumo';
import { GraphIcon } from '@phosphor-icons/react';
import * as api from '../../lib/research/api';
import { labSample, loadLabSample } from '../../lib/research/demo';
import {
  allObligations,
  filterGraph,
  withdrawnIds,
  withoutWithdrawn,
  projectWorkspace,
  sourceName,
} from '../../lib/research/graph';
import type { AgentRun } from '../../types/api';
import { ResearchExperiments } from './ResearchExperiments';
import { ResearchCanvas } from './ResearchCanvas';
import { ResearchDog } from './ResearchDog';
import { ResearchVerificationProgress } from './ResearchVerificationProgress';
import {
  useGraphFilters,
  useGraphQuestions,
  useVerificationRun,
} from '../../lib/research/workspace-hooks';
import { ResearchInspector } from './ResearchInspector';
import { ResearchChecksPanel as ChecksPanel } from './ResearchChecksPanel';
import { DeleteBanner, ResearchHeader } from './ResearchHeader';
import { ResearchSidebar } from './ResearchSidebar';
import { ArgumentChain, MaterialPanel } from './ResearchPanels';
import type { ResearchComposerMode, ResearchMessage } from './ResearchUpload';
import { ResearchChat, isSidebarOpen, withSidebar, type ChatLayout } from './ResearchChat';
import { isAgentActive, useAgentRuns } from '../../lib/research/agent';
const isPhone = () => !!window.matchMedia?.('(max-width: 700px)')?.matches;
export function ResearchWorkspace() {
  const client = useQueryClient();
  const [chatLayout, setChatLayout] = useState<ChatLayout>({
    dock: 'centre',
    open: true,
    details: false,
  });
  const sidebarOpen = isSidebarOpen(chatLayout);
  const chatDocked = chatLayout.dock === 'side';
  const [experimentSourceId, setExperimentSourceId] = useState<string>();
  const [composerMode, setComposerMode] = useState<ResearchComposerMode>('material');
  const [uploadProgress, setUploadProgress] = useState<api.UploadProgress>();
  const [id, setId] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const [target, setTarget] = useState<string>();
  const [tab, setTab] = useState('argument');
  const [collapsed, setCollapsed] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const {
    query,
    setQuery,
    lifecycle,
    setLifecycle,
    source,
    setSource,
    detailedGraph,
    setDetailedGraph,
    showWithdrawn,
    setShowWithdrawn,
    reset: resetFilters,
  } = useGraphFilters();
  const verificationRun = useVerificationRun(id);
  const verificationTrace = verificationRun.trace;
  const graphQuestions = useGraphQuestions(showChain, setBusy);
  const [highlight, setHighlight] = useState<{ ids: string[]; label: string }>();
  const highlighted = useMemo(() => new Set(highlight?.ids ?? []), [highlight]);
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: api.listWorkspaces,
    refetchInterval: 10000,
    retry: 1,
  });
  const current = useQuery({
    queryKey: ['snapshot', id],
    queryFn: () => api.fetchSnapshot(id!),
    enabled: !!id,
    refetchInterval: 3000,
    retry: 1,
  });
  const experiments = useQuery({
    queryKey: ['experiments', id],
    queryFn: () => api.fetchExperiments(id!),
    enabled: !!id,
    refetchInterval: 2000,
    retry: 1,
  });
  const agentRuns = useAgentRuns(id);
  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 15000,
    retry: false,
  });
  const state = current.data;
  const fullGraph = useMemo(
    () => (state ? projectWorkspace(state, detailedGraph, highlighted) : { nodes: [], edges: [] }),
    [state, detailedGraph, highlighted],
  );
  const withdrawn = withdrawnIds(fullGraph).size;
  // Withdrawn claims are left out of the layout too, so hiding them leaves no gaps.
  const layoutGraph = useMemo(
    () => (showWithdrawn || lifecycle === 'rejected' ? fullGraph : withoutWithdrawn(fullGraph)),
    [fullGraph, showWithdrawn, lifecycle],
  );
  const graph = useMemo(
    () => filterGraph(layoutGraph, query, lifecycle, source),
    [layoutGraph, query, lifecycle, source],
  );
  function showChain(ids: string[], label: string) {
    setHighlight({ ids, label });
    setSelected(undefined);
    resetFilters();
    chooseTab('argument');
  }
  useEffect(() => {
    function sync() {
      const saved = new URLSearchParams(window.location.search).get('workspace');
      setId(saved || undefined);
      if (saved && isPhone()) setChatLayout(current => ({ ...current, open: false }));
      setSelected(undefined);
      setTarget(undefined);
      const view = new URLSearchParams(window.location.search).get('view');
      setTab(
        view && ['argument', 'material', 'chain', 'checks', 'experiments'].includes(view)
          ? view
          : 'argument',
      );
    }
    sync();
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  function chooseTab(view: string) {
    setTab(view);
    const url = new URL(window.location.href);
    url.searchParams.set('view', view);
    window.history.replaceState(null, '', url);
  }
  function load(workspace?: string, material = false) {
    setExperimentSourceId(undefined);
    setUploadProgress(undefined);
    setHighlight(undefined);
    setId(workspace);
    setSelected(undefined);
    setTarget(undefined);
    chooseTab(material ? 'material' : 'argument');
    setError('');
    setNotice('');
    resetFilters();
    setDetailedGraph(false);
    setDeleting(false);
    if (workspace && isPhone()) setChatLayout(current => ({ ...current, open: false }));
    const url = new URL(window.location.href);
    if (workspace) url.searchParams.set('workspace', workspace);
    else url.searchParams.delete('workspace');
    window.history.pushState(null, '', url);
  }
  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['snapshot'] }),
      client.invalidateQueries({ queryKey: ['workspaces'] }),
      client.invalidateQueries({ queryKey: ['health'] }),
      client.invalidateQueries({ queryKey: ['agent-runs'] }),
      client.invalidateQueries({ queryKey: ['experiments'] }),
    ]);
  }
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      await refresh();
      setBusy(false);
    }
  }
  async function sendMessage(message: ResearchMessage) {
    if (message.mode === 'ask') return askGraph(message.prompt);
    setBusy(true);
    setError('');
    setNotice('');
    try {
      let workspaceId = id;
      const question =
        message.prompt ||
        'Read the attached documents and map their main claims, evidence, and reasoning into the argument graph.';
      if (!workspaceId) {
        const title =
          message.prompt ||
          message.files[0]?.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ') ||
          'New research';
        const workspace = await api.createWorkspace(title.slice(0, 255));
        workspaceId = workspace.id;
        load(workspaceId);
      }
      chooseTab('argument');
      setSelected(undefined);
      if (message.mode === 'material') {
        const files = [...message.files];
        if (message.prompt)
          files.unshift(
            new File([message.prompt], 'pasted-research.md', { type: 'text/markdown' }),
          );
        await api.uploadResearch(workspaceId, files, true, setUploadProgress);
        return;
      }
      if (message.files.length)
        await api.uploadResearch(workspaceId, message.files, false, setUploadProgress);
      const run = await api.startAgentRun(workspaceId, { ...message.options, question });
      client.setQueryData<AgentRun[]>(['agent-runs', workspaceId], previous => [
        run,
        ...(previous ?? []).filter(item => item.id !== run.id),
      ]);
    } finally {
      await refresh();
      setBusy(false);
    }
  }
  async function askGraph(question: string) {
    if (!id) throw new Error('Open a workspace with a graph first.');
    await graphQuestions.ask(id, question);
  }
  function stopResearch(run: AgentRun) {
    void perform(async () => {
      const cancelled = await api.cancelAgentRun(run.id);
      client.setQueryData<AgentRun[]>(['agent-runs', run.workspace_id], previous =>
        previous?.map(item => (item.id === run.id ? cancelled : item)),
      );
    });
  }
  function show(node: string) {
    setSelected(node);
    if (state?.graph.statements.some(s => s.id === node)) setTarget(node);
    chooseTab('argument');
  }
  async function runVerification(workspace: string) {
    setSelected(undefined);
    resetFilters();
    chooseTab('checks');
    const result = await verificationRun.run(workspace);
    if (!result) return; // abandoned for another workspace
    await refresh();
    setNotice(
      `Verification completed: ${result.issues_opened} issues opened, ${result.issues_resolved} resolved; ${result.obligations_opened} obligations opened, ${result.obligations_resolved} resolved.`,
    );
  }
  const lastVerification = id
    ? (verificationRun.results[id] ?? experiments.data?.verification ?? undefined)
    : undefined;
  const knownObligations = state
    ? allObligations(state).filter(o => o.status === 'open').length
    : 0;
  const targets = state?.graph.statements.filter(s => s.role === 'conclusion') ?? [];
  const activeJobs =
    state?.jobs.filter(j => !['succeeded', 'failed', 'cancelled'].includes(j.status)) ?? [];
  const activeResearch = agentRuns.data?.filter(isAgentActive) ?? [];
  const processingResearch =
    activeJobs.length > 0 ||
    (busy && !!uploadProgress) ||
    (uploadProgress?.stage === 'queued' && !state?.jobs.length);
  const workflowStage = tab === 'experiments' ? 2 : tab === 'checks' ? 1 : 0;
  function changeStage(stage: number) {
    setSelected(undefined);
    chooseTab(stage === 2 ? 'experiments' : stage === 1 ? 'checks' : 'argument');
  }
  return (
    <main className="research-workspace flex h-dvh min-h-[560px] overflow-hidden text-xs">
      <ResearchSidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed(!collapsed)}
        busy={busy}
        workspaces={workspaces.data}
        currentId={id}
        onOpen={workspace => load(workspace)}
        onOpenSample={() =>
          perform(async () => {
            const existing = workspaces.data?.find(w => w.title === labSample.title);
            if (existing) load(existing.id);
            else await loadLabSample(workspace => load(workspace));
            setNotice('DJI_08 is ready. Run the saved sample analysis from Experiments.');
          })
        }
        targets={targets}
        target={target}
        onTarget={statement => {
          setTarget(statement);
          show(statement);
        }}
        apiStatus={
          health.data?.status === 'ok'
            ? 'API connected'
            : health.isPending
              ? 'Connecting…'
              : 'API unavailable'
        }
      />
      <section className="research-main flex min-w-0 flex-1 flex-col">
        <ResearchHeader
          title={state?.workspace.title}
          stage={workflowStage}
          hasResearch={!!state?.sources.length}
          verified={!!experiments.data?.verified}
          onStage={changeStage}
          busy={busy}
          workspaceId={id}
          workspaces={workspaces.data}
          onOpen={workspace => load(workspace)}
          chatToggle={
            id && !['experiments', 'checks'].includes(tab)
              ? {
                  open: sidebarOpen,
                  docked: chatDocked,
                  onToggle: () =>
                    setChatLayout(current => withSidebar(current, !isSidebarOpen(current))),
                }
              : undefined
          }
          materialActive={tab === 'material'}
          hasState={!!state}
          onMaterial={() => {
            chooseTab('material');
            setSelected(undefined);
          }}
          onExport={() =>
            state && api.downloadSnapshot({ ...state, experiments: experiments.data })
          }
          onRefresh={() => perform(refresh)}
          onDelete={() => setDeleting(true)}
        />
        {id && (
          <div className="flex min-h-[43px] shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line bg-zinc-50 px-4 py-1 [&_[role=tab]]:text-[11px]">
            <Tabs
              size="sm"
              variant="underline"
              value={tab}
              onValueChange={value => {
                chooseTab(value);
                setSelected(undefined);
              }}
              tabs={[
                { value: 'argument', label: 'Argument' },
                { value: 'chain', label: 'Chain' },
                { value: 'checks', label: 'Verification' },
                { value: 'experiments', label: 'Experiments' },
              ]}
            />
            <div className="flex items-center gap-2">
              {!!activeResearch.length && (
                <Button
                  size="xs"
                  variant="ghost"
                  className="text-running!"
                  onClick={() => setChatLayout(current => withSidebar(current, true))}
                >
                  <span className="research-processing-dot mr-2" aria-hidden />
                  Researching
                </Button>
              )}
              {tab === 'argument' && (
                <Button
                  size="xs"
                  variant="outline"
                  aria-pressed={detailedGraph}
                  onClick={() => setDetailedGraph(value => !value)}
                >
                  {detailedGraph ? 'Overview' : 'All evidence'}
                </Button>
              )}
              <span className="text-[10px] text-zinc-500 max-[1000px]:hidden">
                {knownObligations} evidence gaps
                {activeJobs.length ? ` · ${activeJobs.length} extraction jobs` : ''}
              </span>
            </div>
          </div>
        )}
        {deleting && state && (
          <DeleteBanner
            title={state.workspace.title}
            busy={busy}
            onKeep={() => setDeleting(false)}
            onConfirm={() =>
              perform(async () => {
                await api.deleteWorkspace(state.workspace.id);
                client.removeQueries({ queryKey: ['snapshot', state.workspace.id] });
                load();
              })
            }
          />
        )}
        <div className="relative grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_auto] grid-rows-[minmax(0,1fr)_auto]">
          <div className="relative col-start-1 row-start-1 min-h-0 min-w-0 overflow-hidden">
            {!id ? (
              <div className="flex h-full flex-col overflow-y-auto px-6 py-6 sm:px-12">
                <div className="research-introduction mx-auto my-auto w-full max-w-2xl py-4">
                  <a
                    href="https://arxiv.org/abs/1512.03385"
                    target="_blank"
                    rel="noreferrer"
                    className="research-paper-preview mb-7 block"
                    aria-label="Preview Deep Residual Learning for Image Recognition"
                  >
                    <img
                      src="/images/research-paper-preview.png"
                      width={774}
                      height={1000}
                      alt="First page of a research paper, with its title, abstract, and two charts"
                      className="h-full w-full object-cover"
                    />
                  </a>
                  <p className="mb-2 text-xs text-zinc-500">Scientific reasoning, made visible.</p>
                  <h1 className="max-w-xl text-[25px] font-normal leading-[1.4] tracking-[-.025em] sm:text-[30px]">
                    Is your agentic science correct?
                  </h1>
                  <p className="mt-4 max-w-xl text-sm leading-[1.8] text-zinc-500">
                    Map claims to evidence. Expose assumptions and missing support, so scientific
                    reasoning can be challenged and tested.
                  </p>
                  <div className="mt-7 flex flex-wrap items-center gap-3 text-xs text-zinc-500">
                    <Button
                      size="sm"
                      variant="outline"
                      className="rounded-sm! font-normal!"
                      loading={busy}
                      onClick={() =>
                        perform(async () => {
                          await loadLabSample(workspace => load(workspace));
                          setNotice(
                            'DJI_08 protocol added. Run the 30-second sample from Experiments.',
                          );
                        })
                      }
                    >
                      Try the full demo
                    </Button>
                    <span>Paste research text or attach your own paper below.</span>
                  </div>
                  {!!workspaces.data?.length && (
                    <select
                      aria-label="Open existing workspace"
                      className="mt-5 w-full rounded-sm border border-line p-2 min-[701px]:hidden"
                      defaultValue=""
                      onChange={e => load(e.target.value)}
                    >
                      <option value="" disabled>
                        Open an existing workspace
                      </option>
                      {workspaces.data.map(w => (
                        <option key={w.id} value={w.id}>
                          {w.title}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              </div>
            ) : !state ? (
              <div className="p-8 text-zinc-500">
                <p>{current.error?.message ?? 'Loading graph…'}</p>
                <Button
                  className="mt-3"
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => perform(refresh)}
                >
                  Retry connection
                </Button>
                <Button className="ml-3 mt-3" size="sm" variant="ghost" onClick={() => load()}>
                  Back to workspaces
                </Button>
              </div>
            ) : (
              <>
                {tab === 'argument' ? (
                  <>
                    <ResearchCanvas
                      key={`${id}:${detailedGraph}`}
                      storageKey={`${id}:${detailedGraph ? 'evidence' : 'overview'}`}
                      layoutGraph={layoutGraph}
                      graph={
                        processingResearch && !state.graph.statements.length
                          ? { nodes: [], edges: [] }
                          : graph
                      }
                      highlight={highlighted}
                      selected={selected}
                      onSelect={node => {
                        setSelected(node);
                        if (state.graph.statements.some(s => s.id === node)) setTarget(node);
                      }}
                    />
                    <div
                      className={cn(
                        'absolute top-4 left-4 z-5 flex max-w-[calc(100%-32px)] flex-wrap items-center gap-2 rounded-md border border-line bg-white/95 p-2',
                        selected && 'min-[900px]:max-w-[calc(100%-380px)]',
                      )}
                    >
                      <Input
                        size="xs"
                        aria-label="Search graph"
                        placeholder="Search statements or identifiers"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                      />
                      <select
                        aria-label="Graph lifecycle filter"
                        className="rounded border border-line p-1 text-[10px]"
                        value={lifecycle}
                        onChange={e => setLifecycle(e.target.value)}
                      >
                        <option value="">All review states</option>
                        <option value="proposed">Proposed</option>
                        <option value="accepted">Accepted</option>
                        <option value="rejected">Rejected</option>
                      </select>
                      <select
                        aria-label="Graph source filter"
                        className="max-w-32 rounded border border-line p-1 text-[10px]"
                        value={source}
                        onChange={e => setSource(e.target.value)}
                      >
                        <option value="">All sources</option>
                        {state.sources.map(s => (
                          <option key={s.id} value={s.id}>
                            {sourceName(s)}
                          </option>
                        ))}
                      </select>
                      {!!withdrawn && (
                        <label className="flex items-center gap-1 text-[10px] text-zinc-600">
                          <input
                            type="checkbox"
                            checked={showWithdrawn}
                            onChange={e => setShowWithdrawn(e.target.checked)}
                          />
                          Show withdrawn ({withdrawn})
                        </label>
                      )}
                      {(query || lifecycle || source) && (
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => {
                            resetFilters();
                          }}
                        >
                          Reset filters
                        </Button>
                      )}
                    </div>
                    {(!graph.nodes.length ||
                      (processingResearch && !state.graph.statements.length)) && (
                      <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-8">
                        <p className="flex max-w-sm flex-col items-center gap-3 text-center text-[12px] text-zinc-500">
                          {!(query || lifecycle || source) && !!activeResearch.length && (
                            <ResearchDog />
                          )}
                          {query || lifecycle || source
                            ? 'No matching graph objects. Reset filters to see the full argument.'
                            : activeResearch.length
                              ? 'The agent is researching. Claims and evidence will appear here as they are recorded.'
                              : processingResearch
                                ? 'Reading your research. Claims and their connections will appear here as they are extracted.'
                                : 'Ask a question in chat or attach a paper to begin building the graph.'}
                        </p>
                      </div>
                    )}
                    <div className="absolute bottom-5 left-40 z-5 flex flex-wrap gap-2 max-[700px]:bottom-16 max-[700px]:left-5">
                      <Button
                        size="sm"
                        loading={busy}
                        disabled={!state.graph.statements.length}
                        onClick={() => perform(() => runVerification(state.workspace.id))}
                      >
                        Verify research
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                          setComposerMode('material');
                          setChatLayout(current => ({ ...current, open: true }));
                          setSelected(undefined);
                          requestAnimationFrame(() =>
                            document.getElementById('research-prompt')?.focus(),
                          );
                        }}
                      >
                        Add material
                      </Button>
                    </div>
                    {tab === 'argument' && highlight && (
                      <div
                        role="status"
                        aria-label="Highlighted reasoning"
                        className="absolute bottom-5 left-1/2 z-5 flex max-w-[min(560px,calc(100%-180px))] -translate-x-1/2 items-center gap-3 rounded-full border border-blue-200 bg-white/95 px-4 py-2 text-[11px] shadow-sm"
                      >
                        <GraphIcon size={14} className="shrink-0 text-blue-600" />
                        <span className="truncate">
                          Highlighting the reasoning for “{highlight.label}”
                        </span>
                        <Button size="xs" variant="ghost" onClick={() => setHighlight(undefined)}>
                          Clear
                        </Button>
                      </div>
                    )}
                  </>
                ) : tab === 'material' ? (
                  <MaterialPanel
                    key={id}
                    state={state}
                    busy={busy}
                    perform={perform}
                    refresh={refresh}
                    onSelect={show}
                    onArgument={() => {
                      chooseTab('argument');
                      setSelected(undefined);
                    }}
                  />
                ) : tab === 'chain' ? (
                  <ArgumentChain
                    state={state}
                    target={target ?? targets[0]?.id ?? state.graph.statements[0]?.id}
                    onSelect={show}
                  />
                ) : tab === 'experiments' ? (
                  <ResearchExperiments
                    key={id}
                    initialSourceId={experimentSourceId}
                    researching={activeResearch.length > 0}
                    lastQuestion={
                      agentRuns.data?.find(r => r.goal?.question)?.goal?.question ??
                      agentRuns.data?.[0]?.question
                    }
                    onResearchStarted={() => void agentRuns.refetch()}
                    state={state}
                    data={experiments.data}
                    loading={experiments.isPending}
                    error={experiments.error}
                    busy={busy}
                    perform={perform}
                    onSource={show}
                  />
                ) : (
                  <div className="flex h-full min-h-0 flex-col">
                    <div className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-white px-6 py-3">
                      <p className="text-[11px] text-zinc-500">
                        {experiments.data?.verified
                          ? 'Research checks are current. Review any evidence gaps before proceeding.'
                          : 'Research checks are optional. You can review evidence gaps or continue to an experiment.'}
                      </p>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={busy || processingResearch}
                        onClick={() => changeStage(2)}
                      >
                        Continue to experiment
                      </Button>
                    </div>
                    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_340px] max-[1000px]:grid-cols-[minmax(0,1fr)_300px] max-[800px]:grid-cols-1 max-[800px]:grid-rows-[minmax(260px,1fr)_minmax(200px,1fr)]">
                      <div className="relative min-h-0 min-w-0 overflow-hidden border-r border-line">
                        <ResearchCanvas
                          key={`${id}:${detailedGraph}`}
                          storageKey={`${id}:${detailedGraph ? 'evidence' : 'overview'}`}
                          layoutGraph={layoutGraph}
                          graph={graph}
                          onSelect={node => setSelected(node)}
                          verification={
                            verificationTrace?.workspaceId === id ? verificationTrace : undefined
                          }
                        />
                        <ResearchVerificationProgress
                          trace={
                            verificationTrace?.workspaceId === id ? verificationTrace : undefined
                          }
                        />
                      </div>
                      <ChecksPanel
                        compact
                        key={id}
                        state={state}
                        busy={busy}
                        canVerify={!processingResearch && !activeResearch.length}
                        lastVerification={lastVerification}
                        perform={perform}
                        refresh={refresh}
                        onVerify={() => runVerification(state.workspace.id)}
                        onSelect={show}
                        onNotice={setNotice}
                      />
                    </div>
                  </div>
                )}
                {selected && (
                  <ResearchInspector
                    key={selected}
                    state={state}
                    id={selected}
                    busy={busy}
                    perform={perform}
                    refresh={refresh}
                    onClose={() => setSelected(undefined)}
                    onSelect={show}
                  />
                )}
              </>
            )}
          </div>
          <ResearchChat
            mode={composerMode}
            onModeChange={setComposerMode}
            workspaceId={id}
            runs={agentRuns.data ?? []}
            questions={graphQuestions.questions.filter(q => q.workspaceId === id)}
            onHighlight={showChain}
            onExperiment={run => {
              setExperimentSourceId(run.protocol?.source_id);
              changeStage(2);
            }}
            loading={!!id && agentRuns.isPending}
            error={agentRuns.error}
            busy={busy}
            layout={chatLayout}
            suspended={['experiments', 'checks'].includes(tab)}
            onLayout={setChatLayout}
            state={state}
            progress={uploadProgress}
            onSubmit={sendMessage}
            onStop={stopResearch}
            onSelect={show}
            onRetry={() => void agentRuns.refetch()}
            onCancelExtraction={() =>
              void perform(async () => {
                await Promise.all(activeJobs.map(job => api.cancelJob(job.id)));
              })
            }
          />
        </div>
        {(error || workspaces.error || (current.error && state)) && (
          <div
            role="alert"
            className="flex shrink-0 items-center justify-between gap-3 border-t border-red-200 bg-red-50 px-5 py-3 text-[11px] text-fail"
          >
            <span className="break-words">
              {error || workspaces.error?.message || current.error?.message}
            </span>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                setError('');
                void refresh();
              }}
            >
              Retry / dismiss
            </Button>
          </div>
        )}
        {notice && (
          <div
            role="status"
            className="flex shrink-0 items-center justify-between gap-3 border-t border-line bg-white px-5 py-3 text-[11px]"
          >
            <span>{notice}</span>
            <Button size="xs" variant="ghost" onClick={() => setNotice('')}>
              Dismiss
            </Button>
          </div>
        )}
      </section>
    </main>
  );
}
