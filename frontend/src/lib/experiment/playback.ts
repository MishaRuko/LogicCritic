import type { Observation, Status, VerificationResult } from './types';
/** Half-open source windows. Gaps are never silently assigned to a method step. */
export function observationsAt(observations: Observation[], seconds: number): Observation[] {
  return observations.filter(o => seconds >= o.timestampStart && seconds < o.timestampEnd).sort((a, b) => b.timestampStart - a.timestampStart);
}
/** With concurrent steps, keep the preferred step while it is still running; otherwise the most recently started one. */
export function observationAt(observations: Observation[], seconds: number, prefer?: string): Observation | undefined {
  const active = observationsAt(observations, seconds);
  return active.find(o => o.stepId === prefer) ?? active[0];
}

/** Steps performed alongside this one: the agent's explicit report, else windows that overlap by more than a boundary. */
export function parallelTo(observations: Observation[], o: Observation): Observation[] {
  const explicit = observations.some(x => x.concurrentWith);
  return observations.filter(x => x.id !== o.id && (explicit
    ? o.concurrentWith?.includes(x.stepId) || x.concurrentWith?.includes(o.stepId)
    : Math.min(o.timestampEnd, x.timestampEnd) - Math.max(o.timestampStart, x.timestampStart) > .5));
}

export type ParallelBand = { start: number; end: number; count: number };
/** Stretches of the recording where two or more steps run at once, with how many. */
export function parallelBands(observations: Observation[]): ParallelBand[] {
  const cuts = [...new Set(observations.flatMap(o => [o.timestampStart, o.timestampEnd]))].sort((a, b) => a - b);
  const bands: ParallelBand[] = [];
  for (let i = 0; i < cuts.length - 1; i++) {
    const [start, end] = [cuts[i], cuts[i + 1]];
    const active = observations.filter(o => o.timestampStart < end && o.timestampEnd > start);
    const linked = active.filter(o => parallelTo(active, o).length > 0);
    if (linked.length < 2) continue;
    const last = bands.at(-1);
    if (last && last.count === linked.length && last.end === start) last.end = end; else bands.push({ start, end, count: linked.length });
  }
  return bands;
}

export type CoverageStatus = Status | 'pending';
export type CoverageSpan = { start: number; end: number; status: CoverageStatus };
const severity: Record<CoverageStatus, number> = { pending: 0, verified: 1, unverifiable: 2, contradicted: 3 };
/** Flattens step windows into non-overlapping spans (overlaps take the worst status) and totals seconds per status; the rest is footage with no step. */
export function coverage(observations: Observation[], results: Record<string, VerificationResult>, duration: number) {
  const cuts = [...new Set([0, duration, ...observations.flatMap(o => [o.timestampStart, o.timestampEnd])].map(t => Math.max(0, Math.min(duration, t))))].sort((a, b) => a - b);
  const spans: CoverageSpan[] = [];
  for (let i = 0; i < cuts.length - 1; i++) {
    const [start, end] = [cuts[i], cuts[i + 1]];
    const status = observations.filter(o => o.timestampStart < end && o.timestampEnd > start)
      .map(o => results[o.stepId]?.status ?? 'pending')
      .reduce<CoverageStatus | undefined>((worst, s) => !worst || severity[s] > severity[worst] ? s : worst, undefined);
    if (!status) continue;
    const last = spans.at(-1);
    if (last && last.status === status && last.end === start) last.end = end; else spans.push({ start, end, status });
  }
  const seconds = { verified: 0, contradicted: 0, unverifiable: 0, pending: 0, none: 0 };
  for (const s of spans) seconds[s.status] += s.end - s.start;
  seconds.none = Math.max(0, duration - spans.reduce((sum, s) => sum + s.end - s.start, 0));
  return { spans, seconds };
}
