'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, Tabs } from '@cloudflare/kumo';
import { FileTextIcon, DownloadSimpleIcon } from '@phosphor-icons/react';
import { ExecutionWorkspace } from './ExecutionWorkspace';
import { RecordView, downloadJSON } from './RecordView';
import { experimentPresentation } from '../../lib/experiment/bridge';
import { createRecord, publishRecord } from '../../lib/experiment/record';
import { observationAt } from '../../lib/experiment/playback';
import { time } from '../../lib/experiment/demo';
import type { ExperimentRecord, RecordSnapshot } from '../../lib/experiment/types';
import type { ExperimentProtocol, ExperimentRun, SourceWithExcerpts } from '../../types/api';

export function ExperimentDetail({ job, protocol, source, onSource, actions }: { job: ExperimentRun; protocol: ExperimentProtocol; source?: SourceWithExcerpts; onSource: (id: string) => void; actions?: ReactNode }) {
  const [tab, setTab] = useState('execution');
  const [selected, setSelected] = useState(protocol.protocol.steps[0]?.id ?? '');
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState<number>();
  const [record, setRecord] = useState<ExperimentRecord>();
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState('');
  const video = useRef<HTMLVideoElement>(null);
  const { method, run, results } = useMemo(() => experimentPresentation(job, protocol, source, duration), [job, protocol, source, duration]);
  const stored = useQuery({ queryKey: ['experiment-records', job.id], queryFn: async () => {
    const response = await fetch(`/api/experiment-runs/${job.id}/records`);
    if (!response.ok) throw new Error('The saved record could not be loaded.');
    return await response.json() as ExperimentRecord[];
  } });
  const currentRecord = record ?? stored.data?.[0];
  function seek(seconds: number) { const next = Math.max(0, Math.min(run.duration, seconds)); if (video.current) video.current.currentTime = next; setCurrentTime(next); }
  function select(id: string) { video.current?.pause(); setSelected(id); const observation = run.observations.find(o => o.stepId === id); if (observation) seek(observation.timestampStart); }
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.target instanceof Element && event.target.closest('[aria-label="Analysis details"]')) return;
      if (tab !== 'execution' || event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLButtonElement) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); const index = method.requirements.findIndex(r => r.id === selected);
        select(method.requirements[Math.max(0, Math.min(method.requirements.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))].id);
      }
      if (event.key === ' ') { event.preventDefault(); if (playing) video.current?.pause(); else void video.current?.play().catch(() => setNotice('Playback is unavailable for this recording.')); }
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  });
  async function generate() {
    setGenerating(true); setNotice('');
    try {
      let snapshot: RecordSnapshot | undefined;
      const media = video.current;
      if (media && media.readyState >= 2 && media.videoWidth) {
        const canvas = document.createElement('canvas');
        canvas.width = Math.min(960, media.videoWidth); canvas.height = canvas.width * media.videoHeight / media.videoWidth;
        canvas.getContext('2d')?.drawImage(media, 0, 0, canvas.width, canvas.height);
        const active = observationAt(run.observations, media.currentTime, selected);
        snapshot = { image: canvas.toDataURL('image/jpeg', .8), t: media.currentTime, stepId: active?.stepId, caption: active?.summary ?? 'Frame from the experiment recording' };
      }
      const created = await createRecord(run, method, snapshot, results, job.result.observations);
      await publishRecord(created);
      setRecord(created); setTab('record');
    } catch (error) { setNotice(error instanceof Error ? error.message : String(error)); }
    finally { setGenerating(false); }
  }
  return <section aria-label="Experiment results">
    <div className="experiment-heading"><div><h2>{method.title}</h2><p>{job.filename} <span className="sep">·</span> {time(run.duration)} recording <span className="sep">·</span> {run.subtitle}</p></div><div className="flex flex-wrap items-center gap-3">{actions}<Button size="sm" variant="outline" loading={generating} onClick={() => void generate()}><FileTextIcon size={14} className="mr-2"/>Generate record</Button></div></div>
    <div className="tabbar"><Tabs size="sm" variant="underline" value={tab} onValueChange={setTab} tabs={[{ value: 'execution', label: 'Execution' }, { value: 'method', label: 'Method' }, { value: 'record', label: 'Record' }]}/></div>
    {job.mode === 'demo' && <p className="experiment-notice">Sample run · synthetic observations, checked by the real protocol verifier.</p>}
    {!!job.result.summary?.failed_windows && <p className="experiment-notice">{job.result.summary.failed_windows} windows could not be analysed. Coverage is incomplete.</p>}
    {notice && <p role="alert" className="experiment-notice">{notice}</p>}
    {tab === 'execution' ? <div onLoadedMetadata={() => { if (video.current?.duration && Number.isFinite(video.current.duration)) setDuration(video.current.duration); }}>
      <ExecutionWorkspace run={run} method={method} results={results} selected={selected} currentTime={currentTime} playing={playing} videoRef={video} sourceLabel={run.subtitle}
        onSelect={select} onSeek={seconds => { seek(seconds); const active = observationAt(run.observations, seconds, selected); if (active) setSelected(active.stepId); }}
        onTimeUpdate={seconds => { setCurrentTime(seconds); if (playing) { const active = observationAt(run.observations, seconds); if (active) setSelected(active.stepId); } }} onPlaying={setPlaying} onMethod={() => setTab('method')} onNotice={setNotice}/>
    </div> : tab === 'method' ? <div className="method-page"><div className="section-heading"><div><h2>Extracted methodology</h2><span>From {method.source} · {method.requirements.length} source-grounded steps</span></div><Button size="sm" variant="ghost" onClick={() => downloadJSON(method, 'trial-method.json')}><DownloadSimpleIcon size={14} className="mr-2"/>Export</Button></div>
      <Button size="xs" variant="ghost" className="mb-4" onClick={() => onSource(protocol.source_id)}>Open original research source</Button>
      <ol className="method-list">{method.requirements.map(requirement => <li key={requirement.id}><span className="mono">{String(requirement.order).padStart(2, '0')}</span><div><strong>{requirement.title}</strong><p>{requirement.description}</p><blockquote className="source-quote">{requirement.quote}</blockquote>{!!requirement.checks?.length && <ul className="checks">{requirement.checks.map(check => <li key={check}>{check}</li>)}</ul>}<p className="caveats">Source passages: {protocol.step_excerpts[requirement.id]?.join(', ')}</p></div></li>)}</ol>
      <details className="agent-trace"><summary>Analysis provenance</summary><p>Extraction: {protocol.extraction_method}. Run: {job.id}. {run.subtitle}. The verdicts reflect the lab-vision verifier; an unreadable or incomplete observation remains unverifiable.</p></details>
    </div> : currentRecord ? <RecordView record={currentRecord} onStep={id => { setTab('execution'); select(id); }}/>
    : <div className="py-12"><h2>Experiment record</h2><p className="muted mt-3 mb-5">A traceable report of the methodology, observations, findings and what this recording could not establish.</p>{stored.error && <p role="alert" className="text-fail mb-3">{stored.error.message}</p>}<Button size="sm" variant="outline" loading={generating} onClick={() => void generate()}>Generate record</Button></div>}
    {job.result.source_id && <Button size="xs" variant="ghost" className="mt-6" onClick={() => onSource(job.result.source_id!)}>View experiment evidence in the research graph</Button>}
  </section>;
}
