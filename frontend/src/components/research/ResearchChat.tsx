'use client';
import { useCallback, useEffect, useLayoutEffect, useRef, type Dispatch, type SetStateAction } from 'react';
import { AnimatePresence, MotionConfig, animate, motion } from 'framer-motion';
import { Button, cn } from '@cloudflare/kumo';
import { ArrowUpRightIcon, CaretDownIcon, CaretUpIcon, FileTextIcon, MagnifyingGlassIcon, SidebarSimpleIcon, StopIcon, XIcon } from '@phosphor-icons/react';
import { agentStatusLabel, eventDescription, eventTitle, isAgentActive, safeSourceUrl, useAgentEvents } from '../../lib/research/agent';
import type { AgentEvent, AgentRun, Snapshot } from '../../types/api';
import type { UploadProgress } from '../../lib/research/api';
import { ResearchComposer, ResearchProgress, type ResearchComposerMode, type ResearchMessage } from './ResearchUpload';
import { ResearchUncertainty } from './ResearchUncertainty';

export type ChatLayout = { dock: 'centre' | 'side'; open: boolean; details: boolean };
export const isSidebarOpen = (layout: ChatLayout) => layout.dock === 'side' ? layout.open : layout.details;
export const withSidebar = (layout: ChatLayout, open: boolean): ChatLayout => layout.dock === 'side' ? { ...layout, open } : { ...layout, details: open };
const slide = { duration: 0.24, ease: [0.32, 0.72, 0, 1] } as const;

export function ResearchChat({ mode, onModeChange, workspaceId, runs, loading, error, busy, layout, onLayout, state, progress, onSubmit, onStop, onSelect, onRetry, onCancelExtraction, suspended = false }: {
  suspended?: boolean;
  mode: ResearchComposerMode; onModeChange: (mode: ResearchComposerMode) => void;
  workspaceId?: string; runs: AgentRun[]; loading: boolean; error: Error | null; busy: boolean;
  layout: ChatLayout; onLayout: Dispatch<SetStateAction<ChatLayout>>;
  state?: Snapshot; progress?: UploadProgress;
  onSubmit: (message: ResearchMessage) => Promise<void>; onStop: (run: AgentRun) => void;
  onSelect: (id: string) => void; onRetry: () => void; onCancelExtraction: () => void;
}) {
  const feed = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const active = runs.filter(isAgentActive);
  const queued = active.filter(run => run.status === 'queued');
  const running = active.find(run => run.status === 'running');
  const latest = running ?? queued[0] ?? runs[0];
  const entries = [
    ...runs.map(run => ({ id: run.id, createdAt: run.created_at, run, source: undefined })),
    ...(state?.sources ?? []).filter(source => source.origin === 'upload').map(source => ({ id: source.id, createdAt: source.created_at, source, run: undefined })),
  ].sort((a, b) => a.createdAt.localeCompare(b.createdAt) || a.id.localeCompare(b.id));
  const scrollToLatest = useCallback(() => {
    if (feed.current && follow.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, []);
  useEffect(() => {
    follow.current = true;
    scrollToLatest();
  }, [workspaceId, entries.length, scrollToLatest]);

  const dock = workspaceId ? layout.dock : 'centre';
  const sideOpen = !suspended && !!workspaceId && isSidebarOpen(layout);
  const centreOpen = !suspended && dock === 'centre' && (!workspaceId || layout.open);
  const home = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLDivElement>(null);
  const sideSlot = useRef<HTMLDivElement>(null);
  const lastDock = useRef(dock);
  // Move the composer's DOM node between docks instead of re-rendering it, so drafts, attachments and options survive the move.
  useLayoutEffect(() => {
    const node = composer.current, origin = home.current;
    if (dock !== 'side' || !node || !origin) return;
    sideSlot.current?.appendChild(node);
    return () => { origin.appendChild(node); };
  }, [dock]);
  useEffect(() => {
    if (lastDock.current === dock) return;
    lastDock.current = dock;
    if (composer.current) animate(composer.current, { opacity: [0, 1], y: [6, 0] }, slide);
  }, [dock]);

  return <MotionConfig reducedMotion="user">
    <motion.aside id="research-chat" aria-label={dock === 'side' ? 'Research chat' : 'Agent details'} aria-hidden={!sideOpen} inert={!sideOpen} initial={false} animate={{ width: sideOpen ? 380 : 0 }} transition={slide} className={cn(
      'z-10 col-start-2 row-span-2 row-start-1 flex min-h-0 min-w-0 justify-end overflow-hidden max-[1000px]:col-start-1 max-[1000px]:row-span-1',
      sideOpen ? 'max-[1000px]:w-full!' : 'max-[1000px]:hidden',
    )}><div className="flex w-[380px] shrink-0 flex-col border-l border-line bg-zinc-50 max-[1000px]:w-full max-[1000px]:border-l-0">
      {workspaceId && <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div><h2 className="text-xs">{dock === 'side' ? 'Research chat' : 'Agent details'}</h2><p className="mt-1 text-[10px] text-zinc-400">{state?.graph.statements.length ?? 0} claims · {state?.sources.length ?? 0} {state?.sources.length === 1 ? 'source' : 'sources'}</p></div>
        <div className="flex items-center gap-1">{dock === 'side' && running && <Button size="xs" variant="ghost" disabled={busy} icon={<StopIcon size={12}/>} onClick={() => onStop(running)}>Stop</Button>}<Button size="xs" variant="ghost" shape="square" aria-label={dock === 'side' ? 'Close chat sidebar' : 'Close agent details'} icon={<XIcon size={14}/>} onClick={() => onLayout(current => withSidebar(current, false))}/></div>
      </header>}
      {workspaceId && <div ref={feed} role="log" aria-label="Research conversation" aria-live="polite" className="min-h-0 flex-1 space-y-6 overflow-y-auto px-4 py-5 [scrollbar-width:thin]" onScroll={() => { const el = feed.current; if (el) follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64; }}>
        {loading && <p className="text-[11px] text-zinc-400">Loading conversation…</p>}
        {error && <div role="alert" className="text-[11px] text-fail">{error.message}<Button size="xs" variant="ghost" className="mt-2" onClick={onRetry}>Retry connection</Button></div>}
        {!entries.length && !loading && !error && <div className="py-6 text-center">{mode === 'material' ? <FileTextIcon size={22} className="mx-auto text-zinc-400"/> : <MagnifyingGlassIcon size={22} className="mx-auto text-zinc-400"/>}<p className="mt-3 text-xs text-zinc-600">{mode === 'material' ? 'Bring your research into the graph' : 'What would you like to investigate?'}</p><p className="mt-2 text-[11px] leading-relaxed text-zinc-400">{mode === 'material' ? 'Paste text or attach a paper below. Switch to Agent whenever you want to ask a question.' : 'Ask a question below. I’ll gather evidence and build the graph alongside our conversation.'}</p></div>}
        {entries.map(entry => entry.run ? <ResearchTurn key={entry.id} run={entry.run} busy={busy} onStop={() => onStop(entry.run)} onSelect={onSelect} onActivity={scrollToLatest}/> : <article key={entry.id} aria-label={`Added material: ${entry.source!.original_filename}`} className="ml-auto max-w-[94%] rounded-xl rounded-br-sm border border-line bg-white px-3.5 py-3"><p className="mb-2 flex items-center gap-2 text-[10px] text-zinc-500"><FileTextIcon size={14}/>Added material</p><p className="break-words text-xs">{entry.source!.original_filename}</p><p className="mt-2 line-clamp-3 whitespace-pre-wrap break-words text-[11px] leading-5 text-zinc-400">{entry.source!.excerpts[0]?.text.slice(0, 400)}</p><Button size="xs" variant="ghost" className="mt-2" icon={<ArrowUpRightIcon size={12}/>} onClick={() => onSelect(entry.source!.id)}>View source</Button></article>)}
      </div>}
      <div ref={sideSlot} className="shrink-0 px-3 pt-2 pb-3 empty:hidden"/>
    </div></motion.aside>
    <div className="col-start-1 row-start-2 min-w-0">
      <motion.div aria-label="Centred research chat" aria-hidden={!centreOpen} inert={!centreOpen} initial={false} animate={centreOpen ? { height: 'auto', opacity: 1, transitionEnd: { overflow: 'visible' } } : { height: 0, opacity: 0, overflow: 'hidden' }} transition={slide}><div className="px-5 pt-3 pb-5 sm:px-8"><div className="mx-auto w-full max-w-3xl">
        {workspaceId && dock === 'centre' && mode === 'agent' && latest && <div role="status" aria-label="Agent summary" className="mb-3 rounded-lg border border-line bg-white px-4 py-3"><div className="flex items-center justify-between gap-2"><p className="flex items-center gap-2 text-[11px] text-zinc-500">{isAgentActive(latest) && <span className="research-processing-dot" aria-hidden/>}Research agent · {agentStatusLabel(latest.status)}</p><div className="flex gap-1">{running && <Button size="xs" variant="ghost" disabled={busy} icon={<StopIcon size={12}/>} onClick={() => onStop(running)}>Stop</Button>}<Button size="xs" variant="ghost" onClick={() => onLayout(current => withSidebar(current, true))}>View details</Button></div></div>{!sideOpen && <p className={cn('mt-2 line-clamp-2 whitespace-pre-wrap break-words text-xs leading-5', latest.error ? 'text-fail' : 'text-zinc-500')}>{latest.final_report || latest.error || latest.question}</p>}</div>}
        {workspaceId && error && !sideOpen && <div role="alert" className="mb-3 text-[11px] text-fail">{error.message}<Button size="xs" variant="ghost" onClick={onRetry}>Retry connection</Button></div>}
        <div ref={home}><div ref={composer} className="research-upload-stack" data-chat-open="true" data-agent-mode={mode === 'agent'} data-dock={dock}>
          <ResearchUncertainty key={workspaceId ?? 'new'} run={latest} state={state} loading={loading} unavailable={!!error}/>
          <ResearchProgress progress={progress} state={state} busy={busy} onCancel={onCancelExtraction}/>
          <ResearchComposer mode={mode} onModeChange={onModeChange} workspaceId={workspaceId} busy={busy} blocked={mode === 'agent' && (!!workspaceId && loading || !!error)} processing={!!active.length} queued={running ? queued.length : Math.max(0, queued.length - 1)} submit={async message => { await onSubmit(message); follow.current = true; scrollToLatest(); }} actions={workspaceId ? <>
            <Button size="xs" variant="ghost" shape="square" aria-label={dock === 'side' ? 'Move chat to centre' : 'Move chat to sidebar'} title={dock === 'side' ? 'Move chat to centre' : 'Move chat to sidebar'} icon={<SidebarSimpleIcon size={14} mirrored weight={dock === 'side' ? 'fill' : 'regular'}/>} onClick={() => onLayout(current => ({ dock: current.dock === 'side' ? 'centre' : 'side', open: true, details: false }))}/>
            {dock === 'centre' && <Button size="xs" variant="ghost" shape="square" aria-label="Hide chat" title="Hide chat" icon={<CaretDownIcon size={14}/>} onClick={() => onLayout(current => ({ ...current, open: false }))}/>}
          </> : undefined}/>
          <p className="mt-2 text-center text-[9px] text-zinc-400">Enter to send · Shift+Enter for a new line · {mode === 'agent' ? 'Agent research uses paid model tokens' : 'Extraction uses paid model tokens'}</p>
        </div></div>
      </div></div></motion.div>
      <AnimatePresence initial={false}>{workspaceId && !layout.open && !suspended && <motion.div key="show-chat" initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={slide} className="overflow-hidden">
        <div className="flex justify-center border-t border-line py-2"><Button size="xs" variant="ghost" icon={<CaretUpIcon size={12}/>} onClick={() => onLayout(current => ({ ...current, open: true }))}>Show chat</Button></div>
      </motion.div>}</AnimatePresence>
    </div>
  </MotionConfig>;
}

function ResearchTurn({ run, busy, onStop, onSelect, onActivity }: { run: AgentRun; busy: boolean; onStop: () => void; onSelect: (id: string) => void; onActivity: () => void }) {
  const trace = useAgentEvents(run);
  const events = trace.data ?? [];
  const activity = events.filter(event => !['thinking', 'turn', 'server_block', 'assistant_text', 'run_finished'].includes(event.type));
  const notes = events.filter(event => event.type === 'assistant_text');
  useEffect(() => { onActivity(); }, [events.length, run.status, run.final_report, onActivity]);
  return <article aria-label={`Research exchange: ${run.question}`} className="space-y-4">
    <div className="ml-auto max-w-[94%] rounded-xl rounded-br-sm border border-line bg-zinc-100 px-3.5 py-3"><p className="mb-1.5 text-[9px] text-zinc-400">You</p><p className="whitespace-pre-wrap break-words text-xs leading-6">{run.question}</p></div>
    <div className="min-w-0"><div className="mb-3 flex items-center justify-between gap-2"><p className="flex items-center gap-2 text-[11px] text-zinc-500">{isAgentActive(run) ? <span className="research-processing-dot" aria-hidden/> : <MagnifyingGlassIcon size={13}/>}Research agent<span className="text-[9px] text-zinc-400">{agentStatusLabel(run.status)}</span></p>{run.status === 'queued' && <Button size="xs" variant="ghost" disabled={busy} onClick={onStop}>Cancel</Button>}</div>
      {run.status === 'queued' && <p role="status" className="text-[11px] leading-relaxed text-zinc-400">Queued for research. Messages are processed in order.</p>}
      {activity.length > 0 && <details open={isAgentActive(run)} className="mb-3 rounded-md border border-line bg-white px-3 py-2"><summary className="cursor-pointer text-[10px] text-zinc-500">Research activity · {activity.length} {activity.length === 1 ? 'step' : 'steps'}</summary><div className="mt-3 space-y-3">{activity.map(event => <ChatActivity key={event.seq} event={event}/>)}</div></details>}
      {trace.error && <p role="alert" className="mb-3 text-[11px] text-fail">{trace.error.message}<Button size="xs" variant="ghost" onClick={() => void trace.refetch()}>Retry activity</Button></p>}
      {!run.final_report && notes.map(event => <p key={event.seq} className="mb-3 whitespace-pre-wrap break-words text-xs leading-6">{String(event.payload.text ?? '')}</p>)}
      {run.final_report && <p className="whitespace-pre-wrap break-words text-xs leading-6">{run.final_report}</p>}
      {run.status === 'running' && !notes.length && !activity.length && <p className="text-[11px] text-zinc-400">Reading the conversation and gathering evidence…</p>}
      {run.error && <p role="alert" className="mt-3 whitespace-pre-wrap text-[11px] leading-relaxed text-fail">{run.error}</p>}
      {run.status === 'cancelled' && <p className="mt-2 text-[11px] text-zinc-400">Research stopped. Evidence already recorded remains in the graph.</p>}
      {(run.certainty || run.final_statement_id) && <div className="mt-3 flex flex-wrap items-center gap-2">{run.certainty && <span className={cn('rounded border border-line bg-white px-2 py-1 text-[10px]', run.certainty === 'established' ? 'text-pass' : 'text-warn')}>{run.certainty}{run.mode === 'baseline' ? ' · unverified' : ''}</span>}{run.final_statement_id && <Button size="xs" variant="ghost" icon={<ArrowUpRightIcon size={12}/>} onClick={() => onSelect(run.final_statement_id!)}>Inspect conclusion</Button>}</div>}
      {run.status !== 'queued' && <p className="mt-3 text-[9px] text-zinc-400">{run.usage.turns ?? 0} / {run.budgets.max_turns} steps · {run.usage.web_searches ?? 0} / {run.budgets.max_web_searches} searches{typeof run.usage.cost_usd === 'number' ? ` · ~$${run.usage.cost_usd.toFixed(3)}` : ''}</p>}
    </div>
  </article>;
}

function ChatActivity({ event }: { event: AgentEvent }) {
  const description = eventDescription(event);
  const criteria = Array.isArray(event.payload.criteria) ? event.payload.criteria : [];
  const results = event.type === 'web_results' && Array.isArray(event.payload.results) ? event.payload.results as Record<string, unknown>[] : [];
  return <div className="border-l border-line pl-2.5"><p className={cn('text-[10px]', event.payload.is_error ? 'text-fail' : 'text-zinc-500')}>{eventTitle(event)}</p>
    {description && <p className="mt-1 whitespace-pre-wrap break-words text-[10px] leading-5 text-zinc-400">{description}</p>}
    {!!criteria.length && <ul className="mt-1 list-disc pl-3 text-[10px] leading-5 text-zinc-400">{criteria.map((criterion, index) => <li key={index}>{String(criterion)}</li>)}</ul>}
    {results.map((result, index) => { const url = safeSourceUrl(result.url); return url ? <a key={index} href={url} target="_blank" rel="noreferrer" className="mt-1 block break-words text-[10px] text-zinc-500 underline underline-offset-2">{String(result.title || new URL(url).hostname)}</a> : null; })}
    {['check', 'finalization'].includes(event.type) && <details className="mt-1"><summary className="cursor-pointer text-[9px] text-zinc-400">Verifier details</summary><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all text-[9px] text-zinc-500">{JSON.stringify(event.payload, null, 2)}</pre></details>}
  </div>;
}
