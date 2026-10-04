'use client';

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Tooltip } from '@cloudflare/kumo';
import { motion, useReducedMotion } from 'framer-motion';
import { time } from '../../lib/experiment/demo';
import { parallelBands, parallelTo } from '../../lib/experiment/playback';
import { StatusIcon } from './Status';
import type { MethodContract, Run, VerificationResult } from '../../lib/experiment/types';

/** Zoom so the shortest step is readable, but never past MAX_SCALE or below fit-to-width. */
const MIN_SEGMENT_PX = 140, MAX_SCALE = 40, TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600], FADE = 32;
export type TimelineView = { start: number; end: number };

export function Timeline({ run, method, results, selected, currentTime, playing, onSelect, onSeek, onView }: {
  run: Run; method: MethodContract; results: Record<string, VerificationResult>;
  selected: string; currentTime: number; playing: boolean; onSelect: (id: string) => void; onSeek: (t: number) => void;
  onView: (view: TimelineView) => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const pointerStart = useRef<number | undefined>(undefined);
  const dragged = useRef(false);
  const userScrolledAt = useRef(0);
  const [viewWidth, setViewWidth] = useState(0);
  const [edges, setEdges] = useState({ left: false, right: false });
  const [hoverTime, setHoverTime] = useState<number>();
  const reduced = useReducedMotion();
  const laneEnds: number[] = [];
  const lanes = new Map<string, number>();
  // Concurrent steps stack into lanes; at equal starts the longer (background) step takes the upper lane.
  for (const observation of [...run.observations].sort((a, b) => a.timestampStart - b.timestampStart || b.timestampEnd - a.timestampEnd)) {
    let lane = laneEnds.findIndex(end => end <= observation.timestampStart);
    if (lane < 0) lane = laneEnds.length;
    laneEnds[lane] = observation.timestampEnd; lanes.set(observation.id, lane);
  }
  const compact = laneEnds.length > 2, laneHeight = compact ? 48 : 68;
  const bands = parallelBands(run.observations);
  const selectedObservation = run.observations.find(o => o.stepId === selected);
  const linked = new Set(selectedObservation ? parallelTo(run.observations, selectedObservation).map(o => o.id) : []);
  const unlocated = method.requirements.filter(r => !run.observations.some(o => o.stepId === r.id));
  const shortest = Math.min(run.duration, ...run.observations.map(o => Math.max(.5, o.timestampEnd - o.timestampStart)));
  const scale = Math.max(viewWidth / run.duration, Math.min(MAX_SCALE, MIN_SEGMENT_PX / shortest));
  const width = run.duration * scale;
  const step = TICK_STEPS.find(s => s * scale >= 72) ?? 600;
  const ticks = Array.from({ length: Math.floor(run.duration / step) + 1 }, (_, i) => i * step).filter(t => t === 0 || t * scale + 20 <= width);
  const scrollable = width > viewWidth + 1;

  useLayoutEffect(() => {
    const v = viewport.current!;
    const observer = new ResizeObserver(() => setViewWidth(v.clientWidth));
    setViewWidth(v.clientWidth); observer.observe(v);
    return () => observer.disconnect();
  }, []);
  function sync() {
    const v = viewport.current; if (!v || !scale) return;
    setEdges({ left: v.scrollLeft > 2, right: v.scrollLeft + v.clientWidth < v.scrollWidth - 2 });
    onView({ start: v.scrollLeft / scale, end: Math.min(run.duration, (v.scrollLeft + v.clientWidth) / scale) });
  }
  useLayoutEffect(sync, [scale, viewWidth, run.id]);
  function scrollTo(left: number) { viewport.current?.scrollTo({ left, behavior: reduced ? 'instant' : 'smooth' }); }
  // Keep the playhead in view unless the user is scrolling the timeline themselves.
  useEffect(() => {
    const v = viewport.current; if (!v || !scrollable || dragging.current || Date.now() - userScrolledAt.current < 2500) return;
    const x = currentTime * scale;
    if (x < v.scrollLeft + FADE || x > v.scrollLeft + v.clientWidth - FADE * 1.5) scrollTo(x - v.clientWidth * (playing ? .2 : .5));
  }, [currentTime, scale, scrollable, playing]);
  useEffect(() => {
    const v = viewport.current, o = run.observations.find(o => o.stepId === selected); if (!v || !o || !scrollable) return;
    const [a, b] = [o.timestampStart * scale, o.timestampEnd * scale];
    if (a < v.scrollLeft || b > v.scrollLeft + v.clientWidth) scrollTo((a + b) / 2 - v.clientWidth / 2);
  }, [selected]);

  function timestamp(clientX: number) {
    const rect = track.current!.getBoundingClientRect();
    return Math.max(0, Math.min(run.duration, (clientX - rect.left) / rect.width * run.duration));
  }
  const userScroll = () => { userScrolledAt.current = Date.now(); };
  const mask = edges.left || edges.right ? `linear-gradient(to right, ${edges.left ? 'transparent' : '#000'}, #000 ${FADE}px, #000 calc(100% - ${FADE}px), ${edges.right ? 'transparent' : '#000'})` : undefined;
  return <section className="lens-timeline" aria-label="Execution timeline">
    <div className="timeline-heading"><h2>Execution timeline</h2><div className="timeline-legend"><span><i className="verified"/>Verified</span><span><i className="contradicted"/>Contradicted</span><span><i className="unverifiable"/>Unverifiable</span>{bands.length > 0 && <span><i className="parallel"/>In parallel</span>}</div></div>
    <div className="timeline-viewport" ref={viewport} style={{ maskImage: mask, WebkitMaskImage: mask }} onScroll={sync}
      onWheel={userScroll} onTouchStart={userScroll} onPointerDown={e => { if (e.target === e.currentTarget) userScroll(); }}>
      {viewWidth > 0 && <div className="timeline-content" style={{ width }}>
        <div className="timeline-axis">{ticks.map(t => <span key={t} style={{ left: t * scale }}>{time(t)}</span>)}</div>
        {bands.length > 0 && <div className="parallel-strip" aria-hidden>{bands.map(b => <Tooltip key={b.start} content={`${b.count} steps at once · ${time(b.start)}–${time(b.end)}`} render={
          <span className="parallel-marker" style={{ left: b.start * scale + 1.5, width: Math.max(4, (b.end - b.start) * scale - 3) }}>{(b.end - b.start) * scale > 34 && <span className="mono">{b.count}×</span>}</span>
        }/>)}</div>}
        <div className="evidence-track" ref={track} style={{ height: Math.max(78, laneEnds.length * laneHeight + 16) }}
          onPointerDown={e => { pointerStart.current = e.clientX; dragged.current = false; if ((e.target as HTMLElement).closest('button')) return; dragging.current = true; e.currentTarget.setPointerCapture(e.pointerId); onSeek(timestamp(e.clientX)); }}
          onPointerMove={e => { const t = timestamp(e.clientX); setHoverTime(t); if (pointerStart.current !== undefined && Math.abs(e.clientX - pointerStart.current) > 4) { dragging.current = true; dragged.current = true; e.currentTarget.setPointerCapture(e.pointerId); } if (dragging.current) onSeek(t); }}
          onPointerUp={() => { dragging.current = false; pointerStart.current = undefined; }} onPointerCancel={() => { dragging.current = false; pointerStart.current = undefined; }}
          onClickCapture={e => { if (dragged.current) { e.preventDefault(); e.stopPropagation(); dragged.current = false; } }}
          onPointerLeave={() => { if (!dragging.current) setHoverTime(undefined); }}>
          <div className="track-grid">{ticks.map(t => <span key={t} style={{ left: t * scale }}/>)}</div>
          {bands.map(b => <div key={b.start} className="parallel-band" style={{ left: b.start * scale, width: (b.end - b.start) * scale }}/>)}
          {run.observations.map(o => {
            const r = method.requirements.find(r => r.id === o.stepId); if (!r) return null;
            const status = results[o.stepId]?.status;
            const alongside = parallelTo(run.observations, o).map(x => method.requirements.find(r => r.id === x.stepId)?.order).filter(n => n !== undefined).map(n => n.toString().padStart(2, '0'));
            return <Tooltip key={o.id} content={`${r.title} · ${time(o.timestampStart)}–${time(o.timestampEnd)} · ${status ?? 'pending'}${alongside.length ? ` · alongside ${alongside.join(', ')}` : ''}`} render={
              <motion.button aria-label={`Timeline ${r.title} at ${time(o.timestampStart)}`}
                className={`evidence-segment ${status ?? 'pending'} ${selected === o.stepId ? 'selected' : ''} ${linked.has(o.id) ? 'linked' : ''} ${compact ? 'compact' : ''}`}
                style={{ top: 8 + (lanes.get(o.id) ?? 0) * laneHeight, bottom: 'auto', height: laneHeight - 8, left: o.timestampStart * scale + 1.5, width: Math.max(6, (o.timestampEnd - o.timestampStart) * scale - 3) }}
                initial={false} animate={{ opacity: status ? 1 : .35 }} whileHover={reduced ? {} : { y: -2 }}
                transition={{ duration: reduced ? 0 : .18 }} onClick={() => onSelect(o.stepId)}>
                <span className="segment-title"><span className="mono">{r.order.toString().padStart(2, '0')}</span>{r.title}</span>
                <span className="segment-status"><StatusIcon status={status} size={11}/>{time(o.timestampStart)}–{time(o.timestampEnd)}{alongside.length > 0 && <span className="segment-parallel">∥ {alongside.join(' ')}</span>}</span>
                {selected === o.stepId && <motion.span className="segment-selection" layoutId="timeline-selection" transition={{ type: 'spring', stiffness: 430, damping: 38 }}/>}
              </motion.button>
            }/>;
          })}
          <motion.div className="timeline-cursor" initial={false} animate={{ left: Math.min(run.duration, currentTime) * scale }} transition={{ duration: reduced ? 0 : .15, ease: 'linear' }}><span className="cursor-time mono">{time(currentTime)}</span></motion.div>
          {hoverTime !== undefined && <div className="timeline-hover-time mono" style={{ left: hoverTime * scale }}>{time(hoverTime)}</div>}
        </div>
      </div>}
    </div>
    <input className="timeline-range" type="range" min="0" max={run.duration} step="0.1" value={currentTime} onChange={e => onSeek(Number(e.target.value))} aria-label="Seek experiment video"/>
    <div className="timeline-bottom"><span>{scrollable ? 'Scroll to pan · click a section or drag to explore.' : 'Click a section or drag to explore.'}{bands.length > 0 && ` Stacked sections run at the same time.`}</span>{unlocated.length > 0 && <span>{unlocated.length} {unlocated.length === 1 ? 'step' : 'steps'} {run.subtitle.includes('partial') ? 'outside this excerpt' : 'not found in the recording'}</span>}</div>
  </section>;
}
