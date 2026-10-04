'use client';

import { useRef } from 'react';
import { time } from '../../lib/experiment/demo';
import { coverage } from '../../lib/experiment/playback';
import type { Run, VerificationResult } from '../../lib/experiment/types';
import type { TimelineView } from './Timeline';

const KEY = [['verified', 'Verified'], ['contradicted', 'Contradicted'], ['unverifiable', 'Unverifiable'], ['pending', 'Pending'], ['none', 'No step']] as const;

/** Whole-recording scrubber: how much of the footage each verdict covers. */
export function CoverageBar({ run, results, currentTime, view, onSeek }: {
  run: Run; results: Record<string, VerificationResult>; currentTime: number; view?: TimelineView; onSeek: (t: number) => void;
}) {
  const track = useRef<HTMLDivElement>(null);
  const { spans, seconds } = coverage(run.observations, results, run.duration);
  const pct = (s: number) => `${s / run.duration * 100}%`;
  const share = (k: typeof KEY[number][0]) => k === 'none'
    ? 100 - KEY.slice(0, -1).reduce((sum, [o]) => sum + Math.round(seconds[o] / run.duration * 100), 0)
    : Math.round(seconds[k] / run.duration * 100);
  function seekTo(clientX: number) {
    const rect = track.current!.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(run.duration, (clientX - rect.left) / rect.width * run.duration)));
  }
  return <section className="coverage" aria-label="Verification coverage">
    <div className="coverage-track" ref={track}
      onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); seekTo(e.clientX); }}
      onPointerMove={e => { if (e.currentTarget.hasPointerCapture(e.pointerId)) seekTo(e.clientX); }}>
      {spans.map(s => <span key={s.start} className={`coverage-span ${s.status}`} style={{ left: pct(s.start), width: pct(s.end - s.start) }}/>)}
      {view && view.end - view.start < run.duration - .01 && <span className="coverage-window" style={{ left: pct(view.start), width: pct(view.end - view.start) }}/>}
      <span className="coverage-cursor" style={{ left: pct(Math.min(currentTime, run.duration)) }}/>
    </div>
    <div className="coverage-footer">
      <span className="mono">{time(currentTime)}</span>
      <div className="coverage-key">{KEY.filter(([k]) => seconds[k] > 0 || k !== 'pending').map(([k, label]) =>
        <span key={k} title={`${time(seconds[k])} of ${time(run.duration)}`}><i className={k}/>{label}<strong className="mono">{share(k)}%</strong></span>)}
      </div>
      <span className="mono">-{time(Math.max(0, run.duration - currentTime))}</span>
    </div>
  </section>;
}
