import { Button } from '@cloudflare/kumo';
import type { AgentProtocol, ExperimentProtocol, SourceWithExcerpts } from '../../types/api';

export function agentProtocolSteps(source?: SourceWithExcerpts): AgentProtocol['steps'] {
  if (source?.metadata?.parser !== 'agent_protocol_v1' || !Array.isArray(source.metadata.steps)) return [];
  return source.metadata.steps.filter((step): step is AgentProtocol['steps'][number] =>
    !!step && typeof step.n === 'number' && typeof step.action === 'string' &&
    Array.isArray(step.excerpt_ids) && step.excerpt_ids.every((id: unknown) => typeof id === 'string'));
}

export function ProtocolCitations({ excerptIds, sources, onSource }: { excerptIds: string[]; sources: SourceWithExcerpts[]; onSource: (id: string) => void }) {
  const passages = sources.flatMap(source => source.excerpts.filter(excerpt => excerptIds.includes(excerpt.id)).map(excerpt => ({ source, excerpt })));
  if (!passages.length) return null;
  return <details className="mt-3 text-xs text-zinc-500"><summary className="cursor-pointer">Research evidence · {passages.length} {passages.length === 1 ? 'passage' : 'passages'}</summary>
    {passages.map(({ source, excerpt }) => <div key={excerpt.id} className="mt-3"><Button size="xs" variant="ghost" onClick={() => onSource(source.id)}>{source.title || source.original_filename}</Button><blockquote className="source-quote">{excerpt.text}</blockquote></div>)}
  </details>;
}

export function ProtocolPreview({ source, protocol, sources, onSource }: { source: SourceWithExcerpts; protocol?: ExperimentProtocol; sources: SourceWithExcerpts[]; onSource: (id: string) => void }) {
  const recorded = agentProtocolSteps(source);
  const steps = protocol ? protocol.protocol.steps.map((step, index) => ({ action: step.description, excerpt_ids: recorded.find(item => item.n === index + 1)?.excerpt_ids ?? protocol.step_excerpts[step.id] ?? [] })) : recorded;
  if (!steps.length) return null;
  return <section aria-label="Selected protocol" className="border-b border-line py-5 mb-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h2>{source.title || source.original_filename}</h2><p className="muted mt-2!">{steps.length} steps · {recorded.length ? 'From the research agent' : 'From the research source'}{protocol ? ' · Ready for a recording' : ''}</p></div><Button size="xs" variant="ghost" onClick={() => onSource(source.id)}>Open protocol source</Button></div>
    {typeof source.metadata?.basis === 'string' && source.metadata.basis && <p className="mt-3! text-xs text-zinc-500">{source.metadata.basis}</p>}
    <details className="mt-4 text-xs"><summary className="cursor-pointer text-zinc-500">Review protocol steps</summary><ol className="method-list mt-4">{steps.map((step, index) => <li key={index}><span className="mono">{String(index + 1).padStart(2, '0')}</span><div><p>{step.action}</p><ProtocolCitations excerptIds={step.excerpt_ids} sources={sources} onSource={onSource}/></div></li>)}</ol></details>
  </section>;
}
