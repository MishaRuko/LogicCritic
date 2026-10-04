import { describe, expect, it } from 'vitest';
import { observationAt, parallelBands, parallelTo } from '../src/lib/experiment/playback';
import type { Observation } from '../src/lib/experiment/types';

const obs = (stepId: string, timestampStart: number, timestampEnd: number, concurrentWith?: string[]): Observation => ({
  id: stepId, stepId, timestampStart, timestampEnd, concurrentWith,
  observed: {}, evidence: [], establishes: [], uncertain: [], confidence: .9, provenance: 'claude_agent',
});

describe('concurrent steps', () => {
  it('reports stretches where steps run at once', () => {
    const incubate = obs('s1', 0, 20, ['s2', 's3']), label = obs('s2', 4, 8, ['s1']), wipe = obs('s3', 6, 12, ['s1']);
    const all = [incubate, label, wipe];
    expect(parallelTo(all, label).map(o => o.stepId)).toEqual(['s1']);
    expect(parallelTo(all, incubate).map(o => o.stepId)).toEqual(['s2', 's3']);
    // s2 and s3 overlap only through s1, so 6–8 still counts all three as running together.
    expect(parallelBands(all)).toEqual([{ start: 4, end: 6, count: 2 }, { start: 6, end: 8, count: 3 }, { start: 8, end: 12, count: 2 }]);
  });

  it('keeps the selected step while it runs, else follows the most recent start', () => {
    const all = [obs('s1', 0, 20), obs('s2', 4, 8)];
    expect(observationAt(all, 5)?.stepId).toBe('s2');
    expect(observationAt(all, 5, 's1')?.stepId).toBe('s1');
    expect(observationAt(all, 10, 's2')?.stepId).toBe('s1');
  });

  it('falls back to overlapping windows when the agent gives no explicit report', () => {
    const all = [obs('s1', 0, 5), obs('s2', 4.8, 9), obs('s3', 6, 9)];
    expect(parallelTo(all, all[0])).toEqual([]);
    expect(parallelBands(all)).toEqual([{ start: 6, end: 9, count: 2 }]);
  });
});
