import { describe, expect, it } from 'vitest';
import { coverage, observationAt } from '../../src/lib/experiment/playback';
import fixtures from '../../src/lib/experiment/fixtures/observations.json';
import type { Observation, VerificationResult } from '../../src/lib/experiment/types';
describe('Caption and methodology alignment', () => {
  const observations = fixtures.DJI_16 as Observation[];
  it('selects the documented transfer window at 7 seconds', () => expect(observationAt(observations, 7)?.stepId).toBe('step-02'));
  it('switches captions at the exact beginning of the next window', () => expect(observationAt(observations, 13)?.stepId).toBe('step-03'));
  it('does not label footage in gaps as a protocol action', () => { for (const t of [0, 5.9, 12, 12.9, 15, 15.9, 25.1]) expect(observationAt(observations, t)).toBeUndefined(); });
});
describe('Coverage strip', () => {
  const obs = (stepId: string, timestampStart: number, timestampEnd: number) => ({ id: stepId, stepId, timestampStart, timestampEnd }) as Observation;
  const ok: VerificationResult = { status: 'verified', confidence: 1, evidence: [] };
  const bad: VerificationResult = { status: 'contradicted', confidence: 1, expected: 1, observed: 2, evidence: [] };
  it('totals seconds per status and leaves gaps as uncovered', () => {
    const { spans, seconds } = coverage([obs('a', 0, 10), obs('b', 20, 25)], { a: ok, b: bad }, 40);
    expect(spans).toEqual([{ start: 0, end: 10, status: 'verified' }, { start: 20, end: 25, status: 'contradicted' }]);
    expect(seconds).toMatchObject({ verified: 10, contradicted: 5, none: 25 });
  });
  it('gives overlapping windows the worse status without double counting', () => {
    const { seconds } = coverage([obs('a', 0, 10), obs('b', 5, 15)], { a: ok, b: bad }, 20);
    expect(seconds).toMatchObject({ verified: 5, contradicted: 10, none: 5 });
  });
});
