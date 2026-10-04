'use client';
import { useEffect, useState } from 'react';
import { Button } from '@cloudflare/kumo';
import { ArrowRightIcon, FlaskIcon } from '@phosphor-icons/react';
import { experimentUrl, linkedExperiments, type ExperimentSummary } from '../../lib/experiment/handoff';
import type { Snapshot } from '../../types/api';
import { panelSection } from '../ui/classes';

/** Optional step two: carry a source's method into the experiment check, and see what came back. */
export function ExperimentSection({ state }: { state: Snapshot }) {
  const [source, setSource] = useState(state.sources[0]?.id ?? '');
  const [runs, setRuns] = useState<ExperimentSummary[]>([]);
  // Linked analyses live in this browser, so read them after mounting.
  useEffect(() => { setRuns(linkedExperiments(state.workspace.id)); }, [state.workspace.id]);
  return <section className={panelSection}>
    <h2>Experiment check</h2>
    <p className="mb-3 max-w-xl text-[11px] leading-relaxed text-zinc-500">Optional. Use a source’s method to check a recording of the experiment, step by step. The result is linked back here.</p>
    {state.sources.length ? <div className="flex flex-wrap items-center gap-2">
      {state.sources.length > 1 && <select aria-label="Method source" className="max-w-64 rounded border border-line p-1 text-[11px]" value={source} onChange={e => setSource(e.target.value)}>{state.sources.map(s => <option key={s.id} value={s.id}>{s.original_filename}</option>)}</select>}
      <a href={experimentUrl({ workspace: state.workspace.id, source })}><Button size="sm" variant="outline" icon={<FlaskIcon size={14}/>}>Continue to experiment</Button></a>
    </div> : <p className="text-[11px] text-zinc-500">Add a source document in Material first; its text becomes the method.</p>}
    {runs.length > 0 && <ul className="mt-4 flex flex-col gap-2">{runs.map(({ analysis, counts, flagged, detailNotes }) => <li key={analysis.id} className="rounded border border-line bg-white p-3 text-[11px]">
      <div className="flex items-center justify-between gap-2"><strong className="font-medium">{analysis.method.title}</strong><a className="flex items-center gap-1 text-zinc-500 hover:text-ink" href={`/experiment?analysis=${encodeURIComponent(analysis.id)}`}>Open<ArrowRightIcon size={11}/></a></div>
      <p className="mt-1 text-zinc-500">{analysis.videoName} · {new Date(analysis.createdAt).toLocaleString()}</p>
      <p className="mt-2">{counts.verified} verified · {counts.contradicted} contradicted · {counts.skipped} skipped · {counts.unverifiable} unverifiable{detailNotes ? ` · ${detailNotes} with details unconfirmed` : ''}</p>
      {flagged.length > 0 && <p className="mt-1 text-fail">Flagged: {flagged.join('; ')}</p>}
    </li>)}</ul>}
  </section>;
}
