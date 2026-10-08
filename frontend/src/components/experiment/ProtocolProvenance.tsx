import { Button } from '@cloudflare/kumo';
import type { AgentProtocol, ExperimentProtocol, SourceWithExcerpts } from '../../types/api';
import { sourceName } from '../../lib/research/graph';

export function agentProtocolSteps(source?: SourceWithExcerpts): AgentProtocol['steps'] {
  if (source?.metadata?.parser !== 'agent_protocol_v1' || !Array.isArray(source.metadata.steps))
    return [];
  return source.metadata.steps.filter(
    (step): step is AgentProtocol['steps'][number] =>
      !!step &&
      typeof step.n === 'number' &&
      typeof step.action === 'string' &&
      Array.isArray(step.excerpt_ids) &&
      step.excerpt_ids.every((id: unknown) => typeof id === 'string'),
  );
}

export function ProtocolCitations({
  excerptIds,
  sources,
  onSource,
}: {
  excerptIds: string[];
  sources: SourceWithExcerpts[];
  onSource: (id: string) => void;
}) {
  const passages = sources.flatMap(source =>
    source.excerpts
      .filter(excerpt => excerptIds.includes(excerpt.id))
      .map(excerpt => ({ source, excerpt })),
  );
  if (!passages.length) return null;
  return (
    <details className="mt-3 text-xs text-zinc-500">
      <summary className="cursor-pointer">
        Research evidence · {passages.length} {passages.length === 1 ? 'passage' : 'passages'}
      </summary>
      {passages.map(({ source, excerpt }) => (
        <div key={excerpt.id} className="mt-3">
          <Button size="xs" variant="ghost" onClick={() => onSource(source.id)}>
            {sourceName(source)}
          </Button>
          <blockquote className="source-quote">{excerpt.text}</blockquote>
        </div>
      ))}
    </details>
  );
}

export function ProtocolPreview({
  source,
  protocol,
  sources,
  onSource,
}: {
  source: SourceWithExcerpts;
  protocol?: ExperimentProtocol;
  sources: SourceWithExcerpts[];
  onSource: (id: string) => void;
}) {
  const recorded = agentProtocolSteps(source);
  const steps = protocol
    ? protocol.protocol.steps.map((step, index) => ({
        action: step.description,
        excerpt_ids:
          recorded.find(item => item.n === index + 1)?.excerpt_ids ??
          protocol.step_excerpts[step.id] ??
          [],
      }))
    : recorded;
  if (!steps.length)
    return (
      <section
        aria-label="Selected protocol"
        className="mt-5 flex flex-wrap items-start justify-between gap-3 border-t border-line pt-5"
      >
        <p className="max-w-2xl text-xs leading-5 text-zinc-500">
          The steps are extracted from this paper&rsquo;s methods section when the experiment
          starts, each linked to the passage it comes from.
        </p>
        <Button size="xs" variant="ghost" onClick={() => onSource(source.id)}>
          Open source
        </Button>
      </section>
    );
  return (
    <section aria-label="Selected protocol" className="mt-5 border-t border-line pt-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium">{sourceName(source)}</h3>
          <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
            <span className="setup-tag">{steps.length} steps</span>
            <span className="setup-tag">
              {recorded.length ? 'Written by the research agent' : 'Taken from the paper'}
            </span>
            {protocol && <span className="setup-tag setup-tag-ready">Ready for a recording</span>}
          </div>
        </div>
        <Button size="xs" variant="ghost" onClick={() => onSource(source.id)}>
          Open source
        </Button>
      </div>
      {typeof source.metadata?.basis === 'string' && source.metadata.basis && (
        <p className="mt-3! text-xs leading-5 text-zinc-500">{source.metadata.basis}</p>
      )}
      <details className="mt-4 text-xs">
        <summary className="cursor-pointer text-zinc-500">Review the {steps.length} steps</summary>
        <ol className="method-list mt-4">
          {steps.map((step, index) => (
            <li key={index}>
              <span className="mono">{String(index + 1).padStart(2, '0')}</span>
              <div>
                <p>{step.action}</p>
                <ProtocolCitations
                  excerptIds={step.excerpt_ids}
                  sources={sources}
                  onSource={onSource}
                />
              </div>
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}
