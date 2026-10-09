import { useState, type ReactNode } from 'react';
import { Button, Input, cn } from '@cloudflare/kumo';
import { XIcon } from '@phosphor-icons/react';
import type { Snapshot } from '../../types/api';
import * as api from '../../lib/research/api';
import {
  allObligations,
  describeStatement,
  describeStep,
  humanize,
  readableFinding,
  sourceName,
} from '../../lib/research/graph';
import { inspectorFrame, panelSection } from '../ui/classes';
import type { Perform } from './ResearchPanels';

// The same meanings and colours the canvas uses for links between claims.
const LINK_COLOURS: Record<string, string> = {
  supports: '#16a34a',
  rebuts: '#dc2626',
  undercuts: '#dc2626',
  qualifies: '#d97706',
  specializes: '#d97706',
  revises: '#71717a',
};
const PASSAGE_CHARS = 320;

function Json({ value }: { value: unknown }) {
  return (
    <pre className="mt-2 overflow-auto text-[10px] leading-relaxed whitespace-pre-wrap break-all">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

/** Technical detail kept out of the way: open it to see provenance, identifiers and raw data. */
function Details({ children }: { children: ReactNode }) {
  return (
    <details className={panelSection}>
      <summary className="cursor-pointer text-[11px] text-zinc-500">Technical details</summary>
      <div className="mt-2 text-[10px] text-zinc-500">{children}</div>
    </details>
  );
}

const clip = (text: string, chars = PASSAGE_CHARS) =>
  text.length > chars ? `${text.slice(0, chars).trimEnd()}…` : text;

export function ResearchInspector({
  state,
  id,
  busy,
  perform,
  refresh,
  onClose,
  onSelect,
}: {
  state: Snapshot;
  id: string;
  busy: boolean;
  perform: Perform;
  refresh: () => Promise<void>;
  onClose: () => void;
  onSelect: (id: string) => void;
}) {
  const [reason, setReason] = useState('');
  const [annotationType, setAnnotationType] = useState('required_premise');
  const [annotationValue, setAnnotationValue] = useState(
    '{"satisfied": false, "description": "Describe the missing premise"}',
  );
  const [notice, setNotice] = useState('');
  const excerpts = state.sources.flatMap(s => s.excerpts);
  const statement = state.graph.statements.find(s => s.id === id);
  const step = state.graph.reasoning_steps.find(r => r.id === id);
  const excerpt = excerpts.find(e => e.id === id);
  const source = state.sources.find(s => s.id === id);
  const relation = state.graph.relations.find(r => r.id === id);
  const obligation = allObligations(state).find(o => o.id === id);
  const node = statement ?? step;
  const type = statement ? 'statement' : 'reasoning_step';
  const context = state.contexts.find(c => c.focus_statement.id === id);
  const claimText = (claimId: string) =>
    state.graph.statements.find(s => s.id === claimId)?.text ?? 'A claim no longer in the graph';
  const heading = statement
    ? statement.role === 'conclusion'
      ? 'Conclusion'
      : statement.role === 'objection'
        ? 'Objection'
        : 'Claim'
    : step
      ? 'Reasoning step'
      : source
        ? 'Source'
        : excerpt
          ? 'Source passage'
          : relation
            ? `Link: ${humanize(relation.relation)}`
            : obligation
              ? 'Open gap'
              : 'Source passage';

  /** A claim, step or passage to jump to: its text, clipped, as a button. */
  function jump(target: string, text: string, tone?: string) {
    return (
      <Button
        key={target}
        size="xs"
        variant="ghost"
        className="mt-1.5 h-auto! w-full justify-start! py-1.5! text-left whitespace-normal!"
        onClick={() => onSelect(target)}
      >
        <span className="flex min-w-0 gap-2">
          {tone && (
            <span
              className="mt-1.5 inline-block h-[3px] w-3 shrink-0 rounded"
              style={{ background: tone }}
              aria-hidden
            />
          )}
          <span className="min-w-0">{clip(text, 220)}</span>
        </span>
      </Button>
    );
  }

  /** The passages a claim cites, each with the source it comes from. */
  function passages(ids: string[]) {
    return ids.map(item => {
      const found = excerpts.find(e => e.id === item);
      const from = found && state.sources.find(s => s.id === found.source_id);
      return (
        <button
          key={item}
          className="mt-2 block w-full rounded border-l-2 border-zinc-300 bg-zinc-50 px-3 py-2 text-left hover:bg-zinc-100"
          onClick={() => onSelect(item)}
        >
          <span className="block text-[12px] leading-relaxed text-zinc-700">
            {found ? clip(found.text) : 'Passage not loaded'}
          </span>
          {from && (
            <span className="mt-1.5 block text-[10px] text-zinc-500">{sourceName(from)}</span>
          )}
        </button>
      );
    });
  }

  /** Links between this claim and others: what supports, rebuts or qualifies what. */
  function links(claimId: string) {
    const touching = state.graph.relations.filter(
      r => r.source_node_id === claimId || r.target_node_id === claimId,
    );
    if (!touching.length) return null;
    return (
      <section className={panelSection}>
        <h2>Links to other claims</h2>
        {touching.map(r => {
          const outgoing = r.source_node_id === claimId;
          const other = outgoing ? r.target_node_id : r.source_node_id;
          const verb = humanize(r.relation);
          const doubt = r.metadata.audit_verdict === 'needs_review';
          return (
            <div key={r.id} className="mt-2">
              <p className="text-[10px] text-zinc-500">
                <span style={{ color: LINK_COLOURS[r.relation] }} className="font-semibold">
                  {outgoing ? `This ${verb}` : `${capitalise(verb)} this`}
                </span>
                {doubt && <span className="text-warn"> · unconfirmed</span>}
              </p>
              {jump(other, claimText(other), LINK_COLOURS[r.relation])}
            </div>
          );
        })}
      </section>
    );
  }

  /** The verifier's open findings on this object, in words. */
  function findings(forId: string) {
    const open = allObligations(state).filter(
      o => (o.blocks_node_id ?? o.blocks_statement_id) === forId && o.status === 'open',
    );
    const issues = context?.issues.filter(i => i.status === 'open') ?? [];
    return (
      <section className={panelSection}>
        <h2>Verifier findings</h2>
        {!open.length && !issues.length && (
          <p className="text-[11px] text-zinc-500">Nothing open.</p>
        )}
        {open.map(o => (
          <button
            key={o.id}
            className="mt-2 block w-full rounded border-l-2 border-fail bg-zinc-50 px-3 py-2 text-left text-[12px] leading-relaxed text-zinc-700 hover:bg-zinc-100"
            onClick={() => onSelect(o.id)}
          >
            {clip(readableFinding(o.description), 400)}
            <span className="mt-1 block text-[10px] text-zinc-500">What would resolve it →</span>
          </button>
        ))}
        {issues
          .filter(i => !open.some(o => o.generated_by_rule === i.rule_code))
          .map(i => (
            <p
              key={i.id}
              className="mt-2 rounded border-l-2 border-fail bg-zinc-50 px-3 py-2 text-[12px] text-zinc-700"
            >
              {capitalise(humanize(i.rule_code))}
              {typeof i.details?.message === 'string'
                ? `: ${readableFinding(i.details.message)}`
                : ''}
            </p>
          ))}
      </section>
    );
  }

  function review() {
    if (!node) return null;
    return (
      <section className={panelSection}>
        <h2>Your review</h2>
        <p className="mb-3 text-[10px] text-zinc-500">
          Accepting records that you checked it; it does not make it true. Rejecting withdraws it
          from the argument.
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            disabled={busy || node.lifecycle === 'accepted'}
            onClick={() =>
              perform(async () => {
                await api.review(state.workspace.id, type, id, 'accepted');
                await refresh();
              })
            }
          >
            {node.lifecycle === 'accepted' ? 'Accepted' : 'Accept'}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy || node.lifecycle === 'rejected'}
            onClick={() =>
              perform(async () => {
                await api.review(state.workspace.id, type, id, 'rejected');
                await refresh();
              })
            }
          >
            {node.lifecycle === 'rejected' ? 'Rejected' : 'Reject'}
          </Button>
        </div>
      </section>
    );
  }

  return (
    <aside aria-label="Argument inspector" className={inspectorFrame}>
      <div className="flex items-center justify-between border-b border-line pb-3">
        <h2
          className="text-[11px]"
          style={relation ? { color: LINK_COLOURS[relation.relation] } : undefined}
        >
          {heading}
        </h2>
        <Button
          size="xs"
          variant="ghost"
          shape="square"
          aria-label="Close inspector"
          icon={<XIcon size={14} />}
          onClick={onClose}
        />
      </div>
      <div className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto pb-5 [scrollbar-width:thin] [&_button]:max-w-full [&_button_span]:whitespace-normal [&_button_span]:min-w-0">
        {node && (
          <>
            <p className="mt-3 text-[11px] text-zinc-500">
              {statement ? describeStatement(statement) : step && describeStep(step)}
            </p>
            <p
              className={cn(
                'mt-2 leading-relaxed whitespace-pre-wrap',
                statement?.role === 'conclusion' ? 'text-[14px]' : 'text-[13px]',
              )}
            >
              {statement?.text ?? step?.explanation}
            </p>
          </>
        )}

        {statement && (
          <>
            <section className={panelSection}>
              <h2>Evidence</h2>
              {statement.excerpt_ids.length ? (
                passages(statement.excerpt_ids)
              ) : (
                <p className="text-[11px] text-zinc-500">
                  {context?.reasoning_steps.some(r => r.conclusion_id === statement.id)
                    ? 'Reached by reasoning from other claims (below), not cited directly.'
                    : 'Cites no source passage.'}
                </p>
              )}
            </section>
            {!!context &&
              (context.reasoning_steps.length > 0 ||
                context.upstream_statements.length > 0 ||
                context.downstream_statements.length > 0) && (
                <section className={panelSection}>
                  <h2>In the argument</h2>
                  {context.reasoning_steps
                    .filter(r => r.conclusion_id === statement.id)
                    .map(r => (
                      <div key={r.id}>
                        <p className="mt-2 text-[10px] text-zinc-500">Reached by this reasoning</p>
                        {jump(r.id, r.explanation)}
                      </div>
                    ))}
                  {context.downstream_statements.length > 0 && (
                    <>
                      <p className="mt-3 text-[10px] text-zinc-500">Supports the conclusion</p>
                      {context.downstream_statements.map(s => jump(s.id, s.text))}
                    </>
                  )}
                  {context.upstream_statements.length > 0 && (
                    <>
                      <p className="mt-3 text-[10px] text-zinc-500">Rests on</p>
                      {context.upstream_statements.map(s => jump(s.id, s.text))}
                    </>
                  )}
                </section>
              )}
            {links(statement.id)}
            {findings(statement.id)}
          </>
        )}

        {step && (
          <>
            <section className={panelSection}>
              <h2>From these claims</h2>
              <p className="text-[10px] text-zinc-500">All are needed for the step to hold.</p>
              {step.premise_ids.map(p => jump(p, claimText(p)))}
              <h2 className="mt-4">To this conclusion</h2>
              {jump(step.conclusion_id, claimText(step.conclusion_id))}
            </section>
            {findings(step.id)}
          </>
        )}

        {review()}

        {node && (
          <Details>
            <p>
              Recorded {new Date(node.created_at).toLocaleString()} by{' '}
              {humanize(String(node.provenance?.actor_id ?? node.provenance?.actor_type ?? ''))}
            </p>
            <Json value={node.provenance} />
            <details className="mt-3">
              <summary className="cursor-pointer">Propose an annotation</summary>
              <p className="my-2">
                Annotations add explicit metadata for the verifier, such as a missing premise.
              </p>
              <Input
                size="sm"
                label="Annotation type"
                value={annotationType}
                onChange={e => setAnnotationType(e.target.value)}
              />
              <label className="mt-3 block">
                Annotation value (JSON object)
                <textarea
                  aria-label="Annotation value"
                  className="mt-2 min-h-24 w-full rounded border border-line p-2 font-mono text-[10px]"
                  value={annotationValue}
                  onChange={e => setAnnotationValue(e.target.value)}
                />
              </label>
              <Button
                size="xs"
                className="mt-3"
                disabled={busy || !annotationType.trim()}
                onClick={() =>
                  perform(async () => {
                    const value: unknown = JSON.parse(annotationValue);
                    if (!value || Array.isArray(value) || typeof value !== 'object')
                      throw new Error('Annotation value must be a JSON object.');
                    const result = await api.patchGraph(state.workspace.id, [
                      {
                        op: 'create_annotation',
                        subject_type: type,
                        subject_id: id,
                        type: annotationType.trim(),
                        value: value as Record<string, unknown>,
                        provenance: api.userProvenance,
                      },
                    ]);
                    setNotice(
                      `Annotation recorded in patch ${result.patch_id}. Run verification to see its effect.`,
                    );
                    await refresh();
                  })
                }
              >
                Propose annotation
              </Button>
              {notice && (
                <p role="status" className="mt-3">
                  {notice}
                </p>
              )}
            </details>
            <p className="mt-3 break-all">ID {id}</p>
          </Details>
        )}

        {excerpt && (
          <>
            <blockquote className="mt-4 border-l-2 border-zinc-300 pl-3 text-[12px] leading-relaxed whitespace-pre-wrap">
              {excerpt.text}
            </blockquote>
            {(() => {
              const from = state.sources.find(s => s.id === excerpt.source_id);
              return (
                from && (
                  <section className={panelSection}>
                    <h2>From</h2>
                    {jump(from.id, sourceName(from))}
                  </section>
                )
              );
            })()}
            <Details>
              <p>Passage {excerpt.sequence} of its source, at a fixed location:</p>
              <Json value={excerpt.locator} />
              <p className="mt-3 break-all">ID {id}</p>
            </Details>
          </>
        )}

        {source && (
          <>
            <h3 className="mt-4 text-[14px] leading-snug">{sourceName(source)}</h3>
            <p className="mt-2 text-[11px] text-zinc-500">
              {[
                typeof source.metadata?.journal === 'string' && source.metadata.journal,
                typeof source.metadata?.publication_date === 'string' &&
                  source.metadata.publication_date.slice(0, 4),
                source.metadata?.is_retracted === true && 'Retracted',
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
            {(() => {
              const doi = source.external_ids?.doi;
              const url = doi ? `https://doi.org/${doi}` : source.external_ids?.url;
              return (
                typeof url === 'string' && (
                  <a
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 inline-block text-[11px] underline"
                  >
                    Open the original
                  </a>
                )
              );
            })()}
            {state.validity[id]?.status === 'invalidated' && (
              <p className="mt-3 text-[11px] text-fail">
                Marked invalid: {state.validity[id].reason}
              </p>
            )}
            <section className={panelSection}>
              <h2>Passages ({source.excerpts.length})</h2>
              {source.excerpts.slice(0, 30).map(e => jump(e.id, e.text))}
              {source.excerpts.length > 30 && (
                <p className="mt-2 text-[10px] text-zinc-500">
                  First 30 of {source.excerpts.length} shown.
                </p>
              )}
            </section>
            <section className={panelSection}>
              <h2>Validity</h2>
              <p className="mb-3 text-[10px] text-zinc-500">
                Mark the source invalid (for example retracted) or valid again, with a reason.
                Claims resting on it are re-checked.
              </p>
              <Input
                size="sm"
                label="Reason"
                value={reason}
                onChange={e => setReason(e.target.value)}
              />
              <div className="mt-3 flex flex-wrap gap-2">
                {(['invalidated', 'valid'] as const).map(status => (
                  <Button
                    key={status}
                    size="xs"
                    variant="outline"
                    disabled={busy || !reason.trim()}
                    onClick={() =>
                      perform(async () => {
                        await api.setValidity(state.workspace.id, id, status, reason.trim());
                        await api.verify(state.workspace.id);
                        setReason('');
                        await refresh();
                      })
                    }
                  >
                    {status === 'valid' ? 'Mark valid' : 'Mark invalid'}
                  </Button>
                ))}
              </div>
            </section>
            <Details>
              <p>
                {source.mime_type} · {humanize(source.origin)}
              </p>
              <p className="mt-1 break-all">SHA-256 {source.content_hash}</p>
              <Json value={source.external_ids} />
              <Json value={source.metadata} />
              <p className="mt-3 break-all">ID {id}</p>
            </Details>
          </>
        )}

        {relation && (
          <>
            <section className="mt-3">
              <p className="text-[10px] text-zinc-500">This claim</p>
              {jump(relation.source_node_id, claimText(relation.source_node_id))}
              <p
                className="mt-2 text-[11px] font-semibold"
                style={{ color: LINK_COLOURS[relation.relation] }}
              >
                {humanize(relation.relation)}
              </p>
              {jump(relation.target_node_id, claimText(relation.target_node_id))}
            </section>
            {typeof relation.metadata.generator_rationale === 'string' && (
              <section className={panelSection}>
                <h2>Why they are linked</h2>
                <p className="text-[12px] leading-relaxed">
                  {relation.metadata.generator_rationale}
                </p>
              </section>
            )}
            <section className={panelSection}>
              <h2>Independent check</h2>
              {relation.metadata.audit_verdict === 'needs_review' ? (
                <p className="text-[12px] leading-relaxed text-warn">
                  Unconfirmed: the cited passages do not clearly show this link. Read both claims
                  before relying on it.
                </p>
              ) : relation.metadata.audit_verdict === 'supported' ? (
                <p className="text-[12px] leading-relaxed text-pass">
                  Confirmed from the cited passages.
                </p>
              ) : (
                <p className="text-[12px] text-zinc-500">Not checked yet.</p>
              )}
              {typeof relation.metadata.audit_rationale === 'string' && (
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-600">
                  {relation.metadata.audit_rationale}
                </p>
              )}
            </section>
            <Details>
              <Json value={relation.metadata} />
              <p className="mt-3 break-all">ID {id}</p>
            </Details>
          </>
        )}

        {obligation && (
          <>
            <p className="mt-4 text-[13px] leading-relaxed">
              {readableFinding(obligation.description)}
            </p>
            <section className={panelSection}>
              <h2>What would resolve it</h2>
              <p className="text-[12px] leading-relaxed">
                {readableFinding(obligation.required_condition)}
              </p>
            </section>
            {(obligation.blocks_node_id ?? obligation.blocks_statement_id) && (
              <section className={panelSection}>
                <h2>Holds back</h2>
                {(() => {
                  const target = (obligation.blocks_node_id ?? obligation.blocks_statement_id)!;
                  const stepText = state.graph.reasoning_steps.find(
                    r => r.id === target,
                  )?.explanation;
                  return jump(target, stepText ?? claimText(target));
                })()}
              </section>
            )}
            <Details>
              <p>
                {capitalise(humanize(obligation.kind))} · {obligation.status} · rule{' '}
                {obligation.generated_by_rule}
              </p>
              <p className="mt-3 break-all">ID {id}</p>
            </Details>
          </>
        )}

        {!node && !excerpt && !source && !relation && !obligation && (
          <p className="mt-5 text-[11px] leading-relaxed text-zinc-500">
            The graph links to this passage, but its source could not be loaded. Refresh the
            workspace to retrieve its exact text.
          </p>
        )}
      </div>
    </aside>
  );
}

const capitalise = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
