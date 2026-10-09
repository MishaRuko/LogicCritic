'use client';
import { useState } from 'react';
import { Button, cn } from '@cloudflare/kumo';
import * as api from '../../lib/research/api';
import { allObligations, humanize, readableFinding } from '../../lib/research/graph';
import type { Snapshot, Verification } from '../../types/api';
import { panel, panelSection } from '../ui/classes';

/** The Verification tab's side panel: running the checks, and the evidence gaps they find. */
export function ResearchChecksPanel({
  compact = false,
  state,
  busy,
  canVerify,
  lastVerification,
  perform,
  refresh,
  onVerify,
  onSelect,
  onNotice,
}: {
  compact?: boolean;
  state: Snapshot;
  busy: boolean;
  canVerify: boolean;
  lastVerification?: Verification;
  perform: (action: () => Promise<void>) => Promise<void>;
  refresh: () => Promise<void>;
  onVerify: () => Promise<void>;
  onSelect: (id: string) => void;
  onNotice: (message: string) => void;
}) {
  const [critic, setCritic] = useState<{ checked_steps: number; flagged_steps: number }>();
  const [synthesis, setSynthesis] = useState<{
    proposed_links: number;
    links_needing_review: number;
  }>();
  const obligations = allObligations(state).filter(item => item.status === 'open');
  return (
    <div className={compact ? 'h-full overflow-y-auto px-5 py-6' : panel}>
      <div className="mx-auto max-w-4xl">
        <p className="mb-2 text-[10px] text-zinc-500">Claims → Evidence → Reasoning</p>
        <h1 className="text-2xl tracking-tight">Verify your research</h1>
        <p className="mt-3 max-w-2xl text-xs leading-6 text-zinc-500">
          Check that claims have evidence, conclusions follow from their premises, and causal
          statements have the support they need. Review the gaps before taking the methodology into
          the lab.
        </p>
        <section className="mt-7 border-t border-line py-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm">Research checks</h2>
              <p className="mt-2 text-[11px] leading-5 text-zinc-500">
                Source grounding, missing premises, scope, conflicts, and causal claims.
              </p>
            </div>
            <Button
              size="sm"
              loading={busy}
              disabled={!canVerify || !state.graph.statements.length}
              onClick={() => perform(onVerify)}
            >
              {lastVerification ? 'Run checks again' : 'Verify research'}
            </Button>
          </div>
          {!canVerify && (
            <p className="mt-4 text-[11px] text-running">
              Research is still processing. Run verification when it finishes.
            </p>
          )}
          {lastVerification && (
            <div className="mt-5 grid grid-cols-3 gap-3">
              {[
                [lastVerification.rules_run.length, 'checks run'],
                [obligations.length, 'evidence gaps'],
                [lastVerification.issues_resolved, 'issues resolved'],
              ].map(([value, label]) => (
                <div
                  key={label}
                  className={cn(
                    'py-2',
                    label === 'evidence gaps'
                      ? obligations.length
                        ? 'text-warn'
                        : 'text-pass'
                      : 'text-running',
                  )}
                >
                  <p className="text-4xl">{value}</p>
                  <p className="mt-1 text-[10px] text-zinc-500">{label}</p>
                </div>
              ))}
            </div>
          )}
          <p className="mt-4 text-[10px] leading-5 text-zinc-500">
            These checks identify gaps in the argument. An experiment can help investigate them;
            completing verification does not establish that a scientific claim is true.
          </p>
        </section>
        <section className="mt-5 border-t border-line py-6">
          <h2 className="text-sm">Evidence gaps to review</h2>
          <p className="mt-2 text-[11px] leading-5 text-zinc-500">
            Open a finding to inspect the claim and its supporting evidence.
          </p>
          {obligations.map(obligation => {
            // Many gaps share a description; name the claim or step each one is about.
            const about = obligation.blocks_statement_id ?? obligation.blocks_node_id;
            const target =
              state.graph.statements.find(s => s.id === about)?.text ??
              state.graph.reasoning_steps.find(r => r.id === about)?.explanation;
            return (
              <Button
                key={obligation.id}
                size="sm"
                variant="ghost"
                className="mt-3 h-auto! w-full justify-between! border-b border-line py-3! text-left text-[11px]! whitespace-normal!"
                onClick={() => onSelect(obligation.id)}
              >
                <span className="min-w-0">
                  <span className="block">{readableFinding(obligation.description)}</span>
                  {target && (
                    <span className="mt-1 line-clamp-2 font-normal text-zinc-500">{target}</span>
                  )}
                </span>
                <span className="ml-3 shrink-0 rounded-full bg-warn/10 px-2 py-1 text-[10px] text-warn">
                  Needs review
                </span>
              </Button>
            );
          })}
          {!obligations.length && (
            <p className="mt-4 text-[11px] text-zinc-500">
              {lastVerification
                ? 'No open evidence gaps were found by the current checks.'
                : 'Run verification to identify missing support and assumptions.'}
            </p>
          )}
        </section>
        <details className={panelSection}>
          <summary className="cursor-pointer text-xs text-zinc-500">
            Additional research tools
          </summary>
          <section className="mt-5">
            <h2 className="text-xs">Model review of reasoning</h2>
            <p className="my-3 text-[11px] leading-5 text-zinc-500">
              Ask Claude whether the premise passages establish each conclusion. This uses paid
              model tokens.
            </p>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !canVerify || !state.graph.reasoning_steps.length}
              onClick={() =>
                perform(async () => {
                  const result = await api.checkArguments(state.workspace.id);
                  setCritic(result);
                  await onVerify();
                  onNotice(
                    `Reviewed ${result.checked_steps} reasoning steps; ${result.flagged_steps} need attention.`,
                  );
                })
              }
            >
              Review reasoning
            </Button>
            {critic && (
              <p className="mt-3 text-[11px]">
                {critic.checked_steps} steps checked · {critic.flagged_steps} flagged
              </p>
            )}
          </section>
          <section className="mt-5">
            <h2 className="text-xs">Connect evidence across sources</h2>
            <p className="my-3 text-[11px] leading-5 text-zinc-500">
              Propose and review supporting or conflicting links between your papers. This uses paid
              model tokens.
            </p>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !canVerify || !state.graph.statements.length}
              onClick={() =>
                perform(async () => {
                  const result = await api.synthesize(state.workspace.id);
                  setSynthesis(result);
                  await refresh();
                  onNotice(
                    `${result.proposed_links} evidence links proposed; ${result.links_needing_review} need review. Run verification again to check the updated research.`,
                  );
                })
              }
            >
              Connect sources
            </Button>
            {synthesis && (
              <p className="mt-3 text-[11px]">
                {synthesis.proposed_links} proposed links · {synthesis.links_needing_review} need
                review
              </p>
            )}
            {state.graph.relations.map(relation => (
              <Button
                key={relation.id}
                variant="ghost"
                size="sm"
                className="mt-3 h-auto! w-full justify-start! text-left whitespace-normal!"
                onClick={() => onSelect(relation.id)}
              >
                {humanize(relation.relation)} · Inspect link
              </Button>
            ))}
          </section>
        </details>
      </div>
    </div>
  );
}
