'use client';

import { motion, useReducedMotion } from 'framer-motion';
import { verificationLabels, type VerificationTrace } from '../../lib/research/verification';

export function ResearchVerificationProgress({ trace }: { trace?: VerificationTrace }) {
  const reduced = useReducedMotion();
  const started = trace?.events.find(event => event.type === 'started');
  const rules = started?.type === 'started' ? started.rules : Object.keys(verificationLabels);
  const completed = trace?.events.filter(event => event.type === 'rule_completed') ?? [];
  const latest = trace?.events
    .filter(event => event.type === 'rule_started' || event.type === 'rule_completed')
    .at(-1);
  const findings = completed.flatMap(event =>
    event.type === 'rule_completed' ? event.findings : [],
  );
  const checking = trace?.status === 'running' && latest?.type === 'rule_started';
  // Only a check that is running or just ran here has progress to show; otherwise the panel beside
  // the canvas has the totals, and an idle "0 / 7 checks" read as if nothing had been checked.
  if (!trace) return null;
  return (
    <section
      aria-label="Live graph verification"
      className="pointer-events-none absolute top-4 right-4 left-4 z-5 rounded-md border border-line bg-paper/95 px-4 py-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <p role="status" aria-live="polite">
          {trace?.status === 'failed'
            ? 'Verification interrupted'
            : trace?.status === 'complete'
              ? 'Graph checks complete'
              : 'Following the graph checks'}
        </p>
        <span className="text-[10px] text-zinc-500">
          {completed.length} / {rules.length} checks
        </span>
      </div>
      {latest && (
        <p className="mt-1 text-[10px] text-zinc-500">
          {verificationLabels[latest.rule_code] ?? latest.rule_code} ·{' '}
          {checking
            ? `checking ${latest.node_ids.length} graph objects`
            : latest.type === 'rule_completed'
              ? `${latest.findings.length} findings`
              : ''}{' '}
          · {findings.length} findings{' '}
          {trace?.status === 'complete' ? 'across all checks' : 'so far'}
        </p>
      )}
      <div
        role="progressbar"
        aria-label="Graph verification progress"
        aria-valuemin={0}
        aria-valuemax={rules.length}
        aria-valuenow={completed.length}
        className="mt-3 flex gap-1.5"
      >
        {rules.map(rule => {
          const done = completed.find(
            event => event.type === 'rule_completed' && event.rule_code === rule,
          );
          const warning = done?.type === 'rule_completed' && done.findings.length > 0;
          return (
            <motion.span
              key={rule}
              aria-hidden
              initial={false}
              animate={{
                backgroundColor: warning
                  ? '#c49a52'
                  : done
                    ? '#4d7963'
                    : checking && latest.rule_code === rule
                      ? '#60a5fa'
                      : '#e4e4e7',
              }}
              transition={{ duration: reduced ? 0 : 0.3 }}
              className="h-[3px] min-w-0 flex-1 rounded-full"
            />
          );
        })}
      </div>
      <p className="mt-2 text-[10px] text-zinc-500">
        Blue: checking · Green: checked without findings · Amber: needs review
      </p>
    </section>
  );
}
