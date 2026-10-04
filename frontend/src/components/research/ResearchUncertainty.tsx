'use client';

import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { cn } from '@cloudflare/kumo';
import { CaretDownIcon } from '@phosphor-icons/react';
import { useAgentEvents } from '../../lib/research/agent';
import { allObligations } from '../../lib/research/graph';
import type { AgentEvent, AgentRun, Assurance, AssuranceLevel, Snapshot } from '../../types/api';

const scale: AssuranceLevel[] = ['unexplored', 'exploring', 'contested', 'provisional', 'well_supported', 'settled'];
const colors: Record<AssuranceLevel, string> = {
  unexplored: '#a1a1aa', exploring: '#60a5fa', contested: '#b34d4d',
  provisional: '#c49a52', well_supported: '#7a9e89', settled: '#4d7963',
};

// Trace updates arrive between run polls and can lower assurance as well as raise it.
function latestAssurance(run: AgentRun | undefined, events: AgentEvent[]): Assurance | undefined {
  if (run?.mode === 'baseline') return undefined;
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index];
    const value = event.type === 'assurance' ? event.payload
      : event.type === 'check' ? (event.payload.result as { assurance?: Assurance } | undefined)?.assurance
      : event.type === 'run_finished' ? (event.payload.usage as AgentRun['usage'] | undefined)?.assurance
      : undefined;
    if (value && scale.includes(value.level as AssuranceLevel)) return value as unknown as Assurance;
  }
  return run?.usage.assurance ?? undefined;
}

export function ResearchUncertainty({ run, state, loading = false, unavailable = false }: {
  run?: AgentRun; state?: Snapshot; loading?: boolean; unavailable?: boolean;
}) {
  const trace = useAgentEvents(run);
  const reducedMotion = useReducedMotion();
  const assurance = latestAssurance(run, trace.data ?? []);
  const baseline = run?.mode === 'baseline';
  const pending = !assurance && (loading || !!run && trace.isPending);
  const failed = unavailable || !assurance && !!trace.error;
  const label = baseline ? 'Unverified' : failed ? 'Unavailable' : pending ? 'Loading…'
    : assurance?.label ?? (run && run.status !== 'queued' ? 'Not assessed yet' : 'No position yet');
  const levels = assurance?.scale?.length ? assurance.scale : scale;
  const level = baseline || failed || pending ? -1 : levels.indexOf(assurance?.level ?? 'unexplored');
  const color = colors[assurance?.level ?? 'unexplored'];
  const reasons = assurance?.holding_back ?? [];
  const gaps = state ? allObligations(state).filter(item => item.status === 'open').length : undefined;
  const transition = { duration: reducedMotion ? 0 : 0.35, ease: 'easeOut' as const };

  return <section aria-label="Research uncertainty" className="mb-3 px-1 pt-1">
    <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-2">
      <div className="flex min-w-0 items-center gap-2 text-[11px]">
        <h2 className="text-zinc-500">Uncertainty</h2>
        <span className="text-zinc-300" aria-hidden>·</span>
        <span role="status" aria-live="polite" aria-atomic="true" className={cn('text-zinc-600', !baseline && !failed && assurance?.level === 'contested' && 'text-fail', !baseline && !failed && assurance?.level === 'settled' && 'text-pass')}>{label}</span>
      </div>
      <dl aria-label="Workspace metrics" className="flex items-center gap-4 text-[10px] text-zinc-400">
        <Metric label="claims" value={state?.graph.statements.length} reducedMotion={!!reducedMotion}/>
        <Metric label="sources" value={state?.sources.length} reducedMotion={!!reducedMotion}/>
        <Metric label="open gaps" value={gaps} reducedMotion={!!reducedMotion}/>
      </dl>
    </div>
    <div role="img" aria-label={`Uncertainty: ${label}`} className="mt-3 flex gap-1.5">
      {levels.map((entry, index) => <motion.span key={entry} aria-hidden="true" initial={false}
        animate={{ backgroundColor: index <= level ? color : '#e4e4e7', opacity: index <= level ? 1 : 0.65 }}
        transition={{ ...transition, delay: reducedMotion ? 0 : index * 0.025 }}
        className="h-[3px] min-w-0 flex-1 rounded-full"/>) }
    </div>
    <div aria-hidden="true" className="mt-1.5 flex justify-between text-[9px] text-zinc-400"><span>More uncertain</span><span>More settled</span></div>
    {!!reasons.length && !baseline && !failed && <details key={run?.id} className="group mt-2 text-[10px] leading-5 text-zinc-500">
      <summary className="flex cursor-pointer list-none items-center gap-2 [&::-webkit-details-marker]:hidden">
        <CaretDownIcon size={10} className="shrink-0 transition-transform duration-200 group-open:rotate-180 motion-reduce:transition-none"/>
        <span className="min-w-0 truncate group-open:whitespace-normal">{reasons[0].description}</span>
        {reasons.length > 1 && <span className="shrink-0 text-zinc-400">+{reasons.length - 1} more</span>}
      </summary>
      {reasons.length > 1 && <ul className="mt-1 space-y-1 pl-[18px]">{reasons.slice(1).map((reason, index) => <li key={`${reason.kind}-${index}`}>{reason.description}</li>)}</ul>}
    </details>}
    {baseline && <p className="mt-2 text-[10px] text-zinc-400">This run has no verifier.</p>}
  </section>;
}

function Metric({ label, value, reducedMotion }: { label: string; value?: number; reducedMotion: boolean }) {
  return <div className="flex items-baseline gap-1.5">
    <dt className="order-2">{label}</dt>
    <dd className="relative overflow-hidden text-[11px] text-zinc-600 tabular-nums">
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span key={value ?? 'pending'} initial={{ opacity: 0, y: reducedMotion ? 0 : 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: reducedMotion ? 0 : -5 }} transition={{ duration: reducedMotion ? 0 : 0.2 }} className="inline-block">{value ?? '—'}</motion.span>
      </AnimatePresence>
    </dd>
  </div>;
}
