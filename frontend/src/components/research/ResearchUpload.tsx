'use client';

import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Button, cn } from '@cloudflare/kumo';
import { ArrowUpIcon, CheckIcon, FileTextIcon, GraphIcon, MagnifyingGlassIcon, PaperclipIcon, XIcon } from '@phosphor-icons/react';
import type { UploadProgress } from '../../lib/research/api';
import type { AgentRunInput, Snapshot } from '../../types/api';

export type ResearchComposerMode = 'material' | 'ask' | 'agent';
export interface ResearchMessage { mode: ResearchComposerMode; prompt: string; files: File[]; options: Omit<AgentRunInput, 'question'> }
export function ResearchComposer({ mode, onModeChange, busy, submit, processing = false, workspaceId, queued = 0, blocked = false, actions }: {
  mode: ResearchComposerMode;
  onModeChange: (mode: ResearchComposerMode) => void;
  busy: boolean;
  blocked?: boolean;
  processing?: boolean;
  workspaceId?: string;
  queued?: number;
  submit: (message: ResearchMessage) => Promise<void>;
  actions?: ReactNode;
}) {
  const inputId = useId();
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState('');
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const [fileKey, setFileKey] = useState(0);
  const [kind, setKind] = useState<AgentRunInput['kind']>('question');
  const [maxTurns, setMaxTurns] = useState(30);
  const [maxSearches, setMaxSearches] = useState(10);
  const [criteria, setCriteria] = useState('');
  const [falsifiers, setFalsifiers] = useState('');
  const sending = useRef(false);
  const previousWorkspace = useRef(workspaceId);
  useEffect(() => {
    if (previousWorkspace.current && previousWorkspace.current !== workspaceId) {
      setText(''); setFiles([]); setError(''); setFileKey(key => key + 1);
    }
    previousWorkspace.current = workspaceId;
  }, [workspaceId]);

  function choose(chosen: File[]) {
    setError('');
    setFiles(chosen);
  }

  async function add() {
    const documents = [...files];
    if ((!documents.length && !text.trim()) || busy || blocked || sending.current) return;
    setError('');
    if (mode === 'ask' && !workspaceId) { setError('Add material or run the agent first: there is no graph to ask about yet.'); return; }
    if (mode === 'ask' && documents.length) { setError('Questions are answered from the graph. Switch to Add material to add these files.'); return; }
    if (mode === 'ask' && (text.trim().length < 3 || text.trim().length > 2000)) { setError('Write a question of 3 to 2,000 characters.'); return; }
    if (mode === 'agent' && text.trim() && text.trim().length < 5) { setError('Write a question or prompt with at least 5 characters.'); return; }
    if (mode === 'agent' && text.trim().length > 2000) { setError('Keep agent prompts under 2,000 characters. Switch to Add material to save longer research text.'); return; }
    if (mode === 'material' && new Blob([text.trim()]).size > 10 * 1024 * 1024) { setError('Pasted research is too large. The limit is 10 MB.'); return; }
    const lines = (value: string) => value.split('\n').map(line => line.trim()).filter(Boolean);
    if (mode === 'agent' && (lines(criteria).length > 8 || lines(falsifiers).length > 8)) { setError('Use at most 8 criteria and 8 falsifiers.'); return; }
    if (mode === 'agent' && (!Number.isInteger(maxTurns) || maxTurns < 1 || maxTurns > 60 || !Number.isInteger(maxSearches) || maxSearches < 0 || maxSearches > 25)) { setError('Use 1–60 research steps and 0–25 web searches.'); return; }
    for (const file of documents) {
      if (!/\.(txt|md|markdown|pdf)$/i.test(file.name)) { setError('Choose a PDF, Markdown or text file.'); return; }
      if (!file.size) { setError(`${file.name} is empty. Choose a file with some text.`); return; }
      if (file.size > 10 * 1024 * 1024) { setError(`${file.name} is too large. The limit is 10 MB per file.`); return; }
    }
    sending.current = true;
    try {
      await submit({ mode, prompt: text.trim(), files: documents, options: { kind, mode: 'guarded', completion_criteria: lines(criteria), falsifiers: lines(falsifiers), max_turns: maxTurns, max_web_searches: maxSearches } });
      setFiles([]); setText(''); setFileKey(k => k + 1);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { sending.current = false; }
  }

  return <div aria-label="Research message composer" className={cn('research-composer border bg-white transition-colors', (busy || processing) && 'research-composer-processing', dragging ? 'border-blue-300 bg-blue-50/30' : 'border-zinc-300')} onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (!busy) choose(Array.from(e.dataTransfer.files)); }}>
    <div className="flex items-center gap-1 border-b border-line px-3 py-2"><div role="group" aria-label="Composer mode" className="flex gap-1">
      {(['material', 'ask', 'agent'] as const).map(value => <button key={value} type="button" aria-label={{ material: 'Material mode', ask: 'Ask graph mode', agent: 'Agent mode' }[value]} title={{ material: 'Add papers or text to the graph', ask: 'Ask about the graph; the claims an answer rests on are highlighted', agent: 'Research with the agent; it adds to the graph' }[value]} aria-pressed={mode === value} disabled={busy || value === 'ask' && !workspaceId} className={cn('flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] transition-colors disabled:opacity-50', mode === value ? value === 'material' ? 'bg-zinc-100 text-zinc-800' : 'bg-blue-50 text-blue-700' : 'text-zinc-400 hover:bg-zinc-50 hover:text-zinc-600')} onClick={() => { setError(''); onModeChange(value); }}>{value === 'material' ? <FileTextIcon size={14}/> : value === 'ask' ? <GraphIcon size={14}/> : <MagnifyingGlassIcon size={14}/>}<span>{{ material: 'Add material', ask: 'Ask graph', agent: 'Agent' }[value]}</span></button>)}
    </div>{actions && <div className="ml-auto flex items-center gap-0.5">{actions}</div>}</div>
    {!!files.length && <ul aria-label="Selected documents" className="flex max-h-24 flex-wrap gap-2 overflow-y-auto px-4 pt-3">{files.map((file, index) => <li key={`${file.name}-${index}`} className="flex max-w-full items-center gap-2 rounded-sm border border-line bg-zinc-50 px-3 py-2 text-xs"><FileTextIcon size={16} className="shrink-0 text-zinc-500"/><span className="min-w-0 truncate">{file.name}</span><span className="shrink-0 text-[10px] text-zinc-400">{file.size < 1024 * 1024 ? `${Math.max(1, Math.round(file.size / 1024))} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`}</span><button type="button" aria-label={`Remove ${file.name}`} disabled={busy} className="shrink-0 text-zinc-500" onClick={() => choose(files.filter((_, i) => i !== index))}><XIcon size={14}/></button></li>)}</ul>}
    <textarea id="research-prompt" aria-label="Research message" placeholder={dragging ? 'Drop your paper here' : mode === 'material' ? 'Paste research text, or attach a PDF…' : mode === 'ask' ? 'Ask about the argument: why does a conclusion hold, what does it rest on, what conflicts…' : workspaceId ? 'Ask a follow-up or steer the research…' : 'Ask a research question, or attach a paper…'} disabled={busy} rows={2} className="block max-h-36 min-h-16 w-full resize-none border-0 bg-transparent px-4 pt-4 pb-2 text-sm leading-relaxed outline-none placeholder:text-zinc-400" value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void add(); } }}/>
    {error && <p role="alert" className="px-4 pb-2 text-xs text-fail">{error}</p>}
    <div className="flex items-center justify-between gap-3 px-3 pb-3"><div className="flex min-w-0 items-center gap-3">
      <label htmlFor={inputId} title="Attach a PDF or research notes" className={cn('relative flex h-8 w-8 shrink-0 items-center justify-center rounded-sm text-zinc-500 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-blue-400', busy ? 'opacity-50' : 'cursor-pointer hover:bg-zinc-100')}>
        <PaperclipIcon size={20}/><span className="sr-only">Attach files</span>
        <input id={inputId} key={fileKey} aria-label="Research documents" type="file" accept=".pdf,application/pdf,.md,.markdown,.txt,text/plain,text/markdown" multiple disabled={busy} onChange={e => choose(Array.from(e.target.files ?? []))} className="absolute inset-0 w-full cursor-pointer opacity-0"/>
      </label>
      {mode === 'agent' ? <details className="relative text-[10px] text-zinc-500"><summary className="cursor-pointer">Research options</summary><div className="absolute bottom-7 left-[-40px] z-20 grid max-h-[min(400px,60dvh)] w-64 gap-3 overflow-y-auto rounded-lg border border-line bg-white p-4 shadow-sm">
        <label className="grid gap-1">Goal<select aria-label="Research goal" className="rounded border border-line p-2" value={kind} disabled={busy} onChange={e => setKind(e.target.value as AgentRunInput['kind'])}><option value="question">Answer a question</option><option value="claim">Check a claim</option><option value="hypothesis">Test a hypothesis</option></select></label>
        <label className="grid gap-1">Research steps<input aria-label="Maximum research steps" className="rounded border border-line p-2" type="number" min={1} max={60} value={maxTurns} disabled={busy} onChange={e => setMaxTurns(Number(e.target.value))}/></label>
        <label className="grid gap-1">Web searches<input aria-label="Maximum web searches" className="rounded border border-line p-2" type="number" min={0} max={25} value={maxSearches} disabled={busy} onChange={e => setMaxSearches(Number(e.target.value))}/></label>
        <label className="grid gap-1">Completion criteria<textarea aria-label="Completion criteria" className="rounded border border-line p-2" placeholder="Optional · one per line, up to 8" value={criteria} disabled={busy} onChange={e => setCriteria(e.target.value)}/></label>
        <label className="grid gap-1">Falsifiers<textarea aria-label="Falsifiers" className="rounded border border-line p-2" placeholder="Optional · one per line, up to 8" value={falsifiers} disabled={busy} onChange={e => setFalsifiers(e.target.value)}/></label>
      </div></details> : <span className="text-[10px] text-zinc-400">{mode === 'ask' ? 'Answered from the graph · relevant claims are highlighted' : 'Build a graph from your material'}</span>}
    </div><Button size="sm" variant="primary" shape="square" className="h-8! w-8! rounded-md! bg-blue-600! text-white! hover:bg-blue-700!" icon={<ArrowUpIcon size={17}/>} aria-label="Send message" title={mode === 'material' ? 'Add material and build its graph' : mode === 'ask' ? 'Ask the graph' : processing ? 'Queue a follow-up' : 'Send research question'} loading={busy} disabled={busy || blocked || (!files.length && !text.trim())} onClick={() => void add()}/></div>
    {processing && mode === 'agent' && <p className="px-4 pb-3 text-[10px] text-zinc-400">{queued ? `${queued} follow-up${queued === 1 ? '' : 's'} queued. ` : ''}Keep prompting. Your next message will run after the current research.</p>}
  </div>;
}

export function ResearchProgress({ progress, state, busy, onCancel }: { progress?: UploadProgress; state?: Snapshot; busy: boolean; onCancel: () => void }) {
  const jobs = (state?.jobs ?? []).filter(job => !progress?.jobIds?.length || progress.jobIds.includes(job.id));
  const active = jobs.filter(job => !['succeeded', 'failed', 'cancelled'].includes(job.status));
  if (!progress && !active.length) return null;
  const transferring = progress && ['uploading', 'processing'].includes(progress.stage);
  const latest = [...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const extractionRequested = progress?.stage === 'queued' || !progress;
  const failed = progress?.stage === 'failed' || (!active.length && extractionRequested && latest && ['failed', 'cancelled'].includes(latest.status));
  const complete = !failed && !transferring && !active.length && (progress?.stage === 'saved' || extractionRequested && latest?.status === 'succeeded');
  const completed = active.reduce((sum, job) => sum + job.completed_chunks, 0);
  const total = active.reduce((sum, job) => sum + job.total_chunks, 0);
  const hasClaims = !!state?.graph.statements.length;
  const percent = transferring ? progress.percent : complete ? 100 : total ? Math.min(100, Math.round(completed / total * 100)) : undefined;
  const title = failed ? 'Processing stopped' : transferring ? progress.stage === 'uploading' ? `Uploading ${progress.total} ${progress.total === 1 ? 'file' : 'files'}` : 'Reading the document' : complete ? progress?.stage === 'saved' ? 'Research saved' : 'Argument graph ready' : hasClaims ? 'Building the argument graph' : 'Extracting claims and evidence';
  const detail = failed ? latest?.error ?? 'Your material is still available. Retry from Material.' : transferring ? progress.filename : complete ? progress?.stage === 'saved' ? 'Open Material when you’re ready to build its argument.' : 'Select a claim to explore its evidence and reasoning.' : hasClaims ? `${state!.graph.statements.length} claims added · new connections appear as extraction continues` : active.every(job => job.status === 'queued') ? 'Waiting for extraction to start…' : 'Finding statements and the evidence that supports them…';
  return <div role="status" className={cn('research-progress-card relative overflow-hidden border border-line bg-white px-4 pt-3 pb-5', failed && 'border-red-200')}>
    <div className="flex items-center justify-between gap-4"><div className="min-w-0"><p className="flex items-center gap-2 text-sm font-normal">{complete ? <CheckIcon size={16} className="text-pass"/> : !failed && <span className="research-processing-dot"/>}{title}</p><p className="mt-1.5 truncate text-xs text-zinc-500" title={detail}>{percent !== undefined ? `${percent}% · ` : ''}{detail}</p></div>{!!active.length && <button aria-label="Cancel extraction" disabled={busy} onClick={onCancel} className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-zinc-400 hover:bg-zinc-100"><XIcon size={18}/></button>}</div>
    <div role="progressbar" aria-label={transferring ? 'Document upload progress' : 'Argument extraction progress'} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} className="research-progress-track absolute right-0 bottom-0 left-0 h-0.5 overflow-hidden bg-zinc-100"><div className={cn('research-stage-bar h-full bg-blue-400 transition-[width] duration-700', percent === undefined && 'research-stage-indeterminate', !complete && !failed && 'research-stage-active')} style={{ width: percent !== undefined ? `${percent}%` : '35%' }}/></div>
  </div>;
}

export function ExtractionProgress({ completed, total, queued = false }: { completed: number; total: number; queued?: boolean }) {
  const percent = total > 0 ? Math.min(100, Math.round(completed / total * 100)) : 0;
  return <div className="mt-4"><div className="mb-2 flex items-center justify-between gap-2 text-xs text-zinc-500"><span>{queued ? 'Waiting to start' : percent === 100 ? 'Finishing argument…' : 'Building argument'}</span><span className="tabular-nums">{queued || !total ? 'Queued' : `${percent}%`}</span></div><progress aria-label="Argument extraction progress" value={percent} max={100} className="research-progress block h-0.5 w-full"/></div>;
}
