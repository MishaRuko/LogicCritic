import type { Observation, Status, VerificationResult } from './types';
/** Half-open source windows. Gaps are never silently assigned to a method step. */
export function observationAt(
  observations: Observation[],
  seconds: number,
): Observation | undefined {
  return observations.find(o => seconds >= o.timestampStart && seconds < o.timestampEnd);
}

export type CoverageStatus = Status | 'pending';
export type CoverageSpan = { start: number; end: number; status: CoverageStatus };
const severity: Record<CoverageStatus, number> = {
  pending: 0,
  verified: 1,
  unverifiable: 2,
  contradicted: 3,
};
/** Flattens step windows into non-overlapping spans (overlaps take the worst status) and totals seconds per status; the rest is footage with no step. */
export function coverage(
  observations: Observation[],
  results: Record<string, VerificationResult>,
  duration: number,
) {
  const cuts = [
    ...new Set(
      [0, duration, ...observations.flatMap(o => [o.timestampStart, o.timestampEnd])].map(t =>
        Math.max(0, Math.min(duration, t)),
      ),
    ),
  ].sort((a, b) => a - b);
  const spans: CoverageSpan[] = [];
  for (let i = 0; i < cuts.length - 1; i++) {
    const [start, end] = [cuts[i], cuts[i + 1]];
    const status = observations
      .filter(o => o.timestampStart < end && o.timestampEnd > start)
      .map(o => results[o.stepId]?.status ?? 'pending')
      .reduce<CoverageStatus | undefined>(
        (worst, s) => (!worst || severity[s] > severity[worst] ? s : worst),
        undefined,
      );
    if (!status) continue;
    const last = spans.at(-1);
    if (last && last.status === status && last.end === start) last.end = end;
    else spans.push({ start, end, status });
  }
  const seconds = { verified: 0, contradicted: 0, unverifiable: 0, pending: 0, none: 0 };
  for (const s of spans) seconds[s.status] += s.end - s.start;
  seconds.none = Math.max(0, duration - spans.reduce((sum, s) => sum + s.end - s.start, 0));
  return { spans, seconds };
}
