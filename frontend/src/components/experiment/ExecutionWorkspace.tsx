'use client';

import { useEffect, useRef, useState, type RefObject } from 'react';
import { Button } from '@cloudflare/kumo';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { ArrowUpRightIcon, PauseIcon, PlayIcon, VideoCameraIcon } from '@phosphor-icons/react';
import { readable, time } from '../../lib/experiment/demo';
import { observationAt } from '../../lib/experiment/playback';
import { CheckIconFor, StatusIcon, StatusLabel } from './Status';
import { Timeline, type TimelineView } from './Timeline';
import { CoverageBar } from './CoverageBar';
import type { MethodContract, Observation, Run, VerificationResult } from '../../lib/experiment/types';

export function ExecutionWorkspace({ run, method, results, selected, currentTime, playing, videoRef, sourceLabel, onSelect, onSeek, onTimeUpdate, onPlaying, onMethod, onNotice }: {
  run: Run; method: MethodContract; results: Record<string, VerificationResult>;
  selected: string; currentTime: number; playing: boolean; videoRef: RefObject<HTMLVideoElement | null>; sourceLabel: string;
  onSelect: (id: string) => void; onSeek: (t: number) => void; onTimeUpdate: (t: number) => void;
  onPlaying: (playing: boolean) => void; onMethod: () => void; onNotice: (text: string) => void;
}) {
  const reduced = useReducedMotion();
  const methodScroll = useRef<HTMLDivElement>(null);
  const [aspect, setAspect] = useState(16 / 9);
  const [mediaError, setMediaError] = useState(false);
  const [view, setView] = useState<TimelineView>();
  const r = method.requirements.find(r => r.id === selected) ?? method.requirements[0];
  const o = run.observations.find(o => o.stepId === r.id);
  const current = observationAt(run.observations, currentTime);
  const captionRequirement = current ? method.requirements.find(r => r.id === current.stepId) : undefined;
  const missingSelection = !o && !playing;
  const result = results[r.id];
  const count = (status: string) => Object.values(results).filter(r => r.status === status).length;
  useEffect(() => {
    const container = methodScroll.current;
    const item = container?.querySelector<HTMLElement>(`[data-step="${selected}"]`);
    if (!container || !item) return;
    if (item.offsetTop < container.scrollTop || item.offsetTop + item.offsetHeight > container.scrollTop + container.clientHeight) container.scrollTo({ top: Math.max(0, item.offsetTop - container.clientHeight / 2 + item.offsetHeight / 2), behavior: reduced ? 'instant' : 'smooth' });
  }, [selected, reduced]);
  function seek(t: number) { videoRef.current?.pause(); onSeek(t); }
  const transition = { duration: reduced ? 0 : .18 };
  return <div className="execution">
    <main className="execution-main">
      <div className="video-stage" style={{ aspectRatio: aspect }}>
        <video key={run.video} ref={videoRef} src={run.video || undefined} poster={run.poster} preload="auto" playsInline
          onLoadedMetadata={e => { const v = e.currentTarget; if (v.videoWidth) setAspect(v.videoWidth / v.videoHeight); v.currentTime = currentTime; setMediaError(false); }}
          onTimeUpdate={e => onTimeUpdate(e.currentTarget.currentTime)} onPlay={() => onPlaying(true)} onPause={() => onPlaying(false)} onEnded={() => onPlaying(false)} onError={() => setMediaError(true)}/>
        {mediaError && <div className="video-error"><VideoCameraIcon size={26}/><strong>Video unavailable</strong><span>The recording could not be loaded in this browser.</span></div>}
        <AnimatePresence mode="wait" initial={false}>
          <motion.div className="caption" key={missingSelection ? r.id : current?.stepId ?? 'gap'} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={transition}>
            <div><span className="caption-step">{captionRequirement && !missingSelection ? `Step ${captionRequirement.order.toString().padStart(2, '0')}` : ''}</span><strong>{missingSelection ? `${r.title}: ${run.subtitle.includes('partial') ? 'outside this excerpt' : 'not found in the recording'}` : captionRequirement?.title ?? 'Between steps'}</strong>{current && !missingSelection && <span className="mono">{time(current.timestampStart)}–{time(current.timestampEnd)}</span>}</div>
            <p>{missingSelection ? result.status === 'unverifiable' ? result.reason : 'The recording cannot establish whether this step was followed.' : current ? captionFor(current, currentTime) : 'No methodology step is assigned to this part of the recording.'}</p>
          </motion.div>
        </AnimatePresence>
      </div>
      <div className="video-controls">
        <Button variant="ghost" size="sm" shape="square" aria-label={playing ? 'Pause video' : 'Play video'} onClick={() => { if (playing) videoRef.current?.pause(); else videoRef.current?.play().catch(() => onNotice('Playback is unavailable for this recording.')); }}>{playing ? <PauseIcon size={17} weight="fill"/> : <PlayIcon size={17} weight="fill"/>}</Button>
        <span className="mono">{time(currentTime)} <span className="muted">/ {time(run.duration)}</span></span>
        <span className="video-source">{sourceLabel}</span>
      </div>
      <CoverageBar run={run} results={results} currentTime={currentTime} view={view} onSeek={seek}/>
      <Timeline run={run} method={method} results={results} selected={selected} currentTime={currentTime} playing={playing} onSelect={onSelect} onSeek={seek} onView={setView}/>
      <AnimatePresence mode="wait" initial={false}>
        <motion.section className="step-evidence" key={r.id} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -3 }} transition={transition} aria-label="Step evidence">
          <header><span className="mono muted">{r.order.toString().padStart(2, '0')}</span><h3>{r.title}</h3><StatusLabel status={result.status}/>{o && <Button className="push-right" variant="ghost" size="xs" icon={ArrowUpRightIcon} onClick={() => seek(o.timestampStart)}>Jump to {time(o.timestampStart)}</Button>}</header>
          <div className="evidence-columns">
            <div><span className="label">Method says</span><p>{r.description}</p></div>
            <div><span className="label">Recording shows</span><p>{observedText(o)}</p></div>
          </div>
          {o?.checkResults && <ul className="check-list">{o.checkResults.map(c => <li key={c.check}><CheckIconFor result={c.result}/><div><strong>{c.check}{c.required === false && <em className="check-detail">detail</em>}</strong><span>{c.note}</span></div></li>)}</ul>}
          {!o?.checkResults && result.status === 'unverifiable' && <p className="muted small">Cannot establish: {result.missingEvidence.map(readable).join(', ')}.</p>}
          {result.status === 'contradicted' && !o?.checkResults && <p className="deviation">Expected {describe(result.expected)}; observed {describe(result.observed)}.</p>}
          {o && o.evidence.some(e => e.kind === 'video') && <div className="evidence-times"><span className="label">Evidence</span>{o.evidence.filter(e => e.kind === 'video').map(e => <button key={e.id} onClick={() => seek(e.timestamp)}><span className="mono">{time(e.timestamp)}</span>{e.description}</button>)}</div>}
          {r.caveats?.length ? <p className="caveats"><span className="label">Not establishable from video</span>{r.caveats.join(' · ')}</p> : null}
        </motion.section>
      </AnimatePresence>
    </main>
    <aside className="method-rail">
      <div className="rail-heading"><div><h2>Methodology</h2><span>{method.requirements.length} steps</span></div><Button variant="ghost" size="xs" icon={ArrowUpRightIcon} onClick={onMethod}>Details</Button></div>
      <div className="rail-counts">{(['verified', 'contradicted', 'unverifiable'] as const).map(status => <span key={status} className={status}><strong>{count(status)}</strong>{status.charAt(0).toUpperCase() + status.slice(1)}</span>)}</div>
      <div className="rail-scroll" ref={methodScroll}>{method.requirements.map(requirement => {
        const observation = run.observations.find(o => o.stepId === requirement.id); const status = results[requirement.id]?.status;
        return <button key={requirement.id} data-step={requirement.id} className={`rail-item ${selected === requirement.id ? 'selected' : ''}`} aria-pressed={selected === requirement.id} onClick={() => onSelect(requirement.id)}>
          <span className="mono rail-number">{requirement.order.toString().padStart(2, '0')}</span>
          <div><strong>{requirement.title}</strong><p>{requirement.description}</p><span className="rail-state"><StatusIcon status={status} size={12}/><span className={status}>{status ? status.charAt(0).toUpperCase() + status.slice(1) : 'Pending'}</span><span className="mono">{observation ? `${time(observation.timestampStart)}–${time(observation.timestampEnd)}` : run.subtitle.includes('partial') ? 'Outside clip' : 'Not found'}</span></span></div>
        </button>;
      })}</div>
    </aside>
  </div>;
}

function captionFor(o: Observation, t: number): string {
  const video = o.evidence.filter(e => e.kind === 'video');
  const nearest = [...video].sort((a, b) => Math.abs(a.timestamp - t) - Math.abs(b.timestamp - t))[0];
  return (o.summary && (!nearest || Math.abs(nearest.timestamp - t) > 3) ? o.summary : nearest?.description) ?? o.summary ?? '';
}
function observedText(o?: Observation): string {
  if (!o) return 'This step was not located in the recording.';
  if (o.summary) return o.summary;
  const fields = Object.entries(o.observed).filter(([, v]) => v !== undefined);
  return fields.map(([k, v]) => k === 'durationSeconds' ? time(Number(v)) : readable(String(v))).join(' → ');
}
function describe(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') return Object.values(value).map(v => readable(String(v))).join(', ');
  return String(value);
}
