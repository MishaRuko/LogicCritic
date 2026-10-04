import { describe, expect, it } from 'vitest';
import { LONG_WAIT_SECONDS, verifyRun, waitSeconds } from '../../src/lib/experiment/conformance';
import type { Absence, MethodContract, Observation, ProtocolRequirement } from '../../src/lib/experiment/types';

const method: MethodContract = { schemaVersion: 1, title: 'Mock', version: '1', source: 'mock', requirements: [1, 2, 3, 4, 5].map(n => ({
  id: `s${n}`, order: n, title: `Step ${n}`, description: `Step ${n}`, checks: [`Step ${n} visible`], criticality: 'critical' as const, category: 'action' as const
})) };
const placed = (n: number, start: number, end: number, confidence = .9): Observation => ({
  id: `s${n}-agent`, stepId: `s${n}`, timestampStart: start, timestampEnd: end, observed: {}, establishes: [], uncertain: [], confidence, provenance: 'claude_agent',
  checkResults: [{ check: `Step ${n} visible`, result: 'confirmed', note: '' }], evidence: [{ id: `s${n}-e`, timestamp: start, end: start + 1, description: '', kind: 'video' }]
});
const absent = (n: number, reason: Absence['reason'] = 'not_performed', confidence = .9): Absence => ({ stepId: `s${n}`, reason, confidence, note: `Step ${n} not done` });
const run = (observations: Observation[], absences: Absence[] = [], duration = 60) => verifyRun(method, { observations, absences, duration });
const statuses = (r: ReturnType<typeof run>) => method.requirements.map(x => r[x.id].status);

describe('Sequence rules', () => {
  it('flags nothing on a correct, in-order run', () =>
    expect(statuses(run([placed(1, 0, 10), placed(2, 10, 20), placed(3, 20, 30), placed(4, 30, 40), placed(5, 40, 60)]))).toEqual(Array(5).fill('verified')));
  it('reports a step skipped when the agent saw it not done', () => {
    const r = run([placed(1, 0, 10), placed(2, 10, 20), placed(4, 21, 30), placed(5, 30, 60)], [absent(3)]);
    expect(r.s3).toMatchObject({ status: 'skipped', between: [20, 21], reason: 'Step 3 not done' });
  });
  it('allows a skip between overlapping neighbours', () =>
    expect(run([placed(2, 10, 22), placed(4, 20, 30)], [absent(3)]).s3.status).toBe('skipped'));
  it('no longer needs the neighbours to be adjacent: the agent watched the gap', () =>
    expect(run([placed(2, 10, 20), placed(4, 31, 40)], [absent(3)]).s3).toMatchObject({ status: 'skipped', between: [20, 31] }));
  it('needs the agent to have seen the step not done', () =>
    expect(run([placed(2, 10, 20), placed(4, 20, 30)], [absent(3, 'not_visible')]).s3.status).toBe('unverifiable'));
  it('uses the structure, not the agent\'s confidence number, for skips', () => {
    expect(run([placed(2, 10, 20, .5), placed(4, 20, 30, .5)], [absent(3, 'not_performed', .5)]).s3.status).toBe('skipped');
  });
  it('never calls a long wait skipped: those are cut from recordings', () => {
    const waiting: MethodContract = { ...method, requirements: method.requirements.map(r => r.id === 's3' ? { ...r, description: 'Incubate at room temperature for 20 min.' } : r) };
    expect(verifyRun(waiting, { observations: [placed(2, 10, 20), placed(4, 20, 30)], absences: [absent(3)], duration: 60 }).s3.status).toBe('unverifiable');
    const brief: MethodContract = { ...method, requirements: method.requirements.map(r => r.id === 's3' ? { ...r, description: 'Incubate on ice for 5 seconds.' } : r) };
    expect(verifyRun(brief, { observations: [placed(2, 10, 20), placed(4, 20, 30)], absences: [absent(3)], duration: 60 }).s3.status).toBe('skipped');
  });
  it('reads the longest stated wait from a step', () => {
    expect(waitSeconds('Incubate at room temperature for 20 min.')).toBe(1200);
    expect(waitSeconds('Rapidly transfer to 42°C for exactly 5 seconds.')).toBe(5);
    expect(waitSeconds('Incubate 1-2 hours')).toBe(7200);
    expect(waitSeconds('Add 5 μL of plasmid DNA.')).toBe(0);
    expect(waitSeconds(`Wait ${LONG_WAIT_SECONDS} s`)).toBe(LONG_WAIT_SECONDS);
  });
  it('verifies a step with every check confirmed, whatever the overall confidence', () =>
    expect(run([placed(1, 0, 10, .4)]).s1.status).toBe('verified'));
  it('does not accuse on a low-confidence contradiction or reordering', () => {
    const wrong = { ...placed(2, 10, 20, .6), checkResults: [{ check: 'Step 2 visible', result: 'contradicted' as const, note: 'wrong tube' }] };
    expect(run([placed(1, 0, 10), wrong]).s2.status).toBe('unverifiable');
    expect(run([placed(1, 0, 10), placed(2, 40, 45, .6), placed(3, 10, 20), placed(4, 20, 30)]).s2.status).toBe('verified');
  });
  it('measures the first step from the start of the recording and the last to its end', () => {
    expect(run([placed(2, 5, 10)], [absent(1)]).s1).toMatchObject({ status: 'skipped', between: [0, 5] });
    expect(run([placed(4, 30, 50)], [absent(5)], 60).s5).toMatchObject({ status: 'skipped', between: [50, 60] });
  });
  it('reports a skip among look-alike steps as one of the group', () => {
    const grouped: MethodContract = { ...method, requirements: method.requirements.map(r => ['s1', 's2', 's3'].includes(r.id) ? { ...r, visualGroup: 'reagent addition' } : r) };
    const r = verifyRun(grouped, { observations: [placed(1, 0, 10), placed(2, 10, 20), placed(4, 20, 30)], absences: [absent(3)], duration: 60 });
    expect(r.s3).toMatchObject({ status: 'skipped' });
    expect((r.s3 as { reason: string }).reason).toMatch(/^One of Step 1 \/ Step 2 \/ Step 3 was not performed/);
    expect((run([placed(2, 10, 20), placed(4, 20, 30)], [absent(3)]).s3 as { reason: string }).reason).toBe('Step 3 not done');
  });
  it('never reports a skip from missing footage alone', () =>
    expect(statuses(run([placed(1, 0, 10)]))).toEqual(['verified', 'unverifiable', 'unverifiable', 'unverifiable', 'unverifiable']));
  it('flags only the step that moved out of order', () => {
    const r = run([placed(1, 0, 10), placed(2, 40, 45), placed(3, 10, 20), placed(4, 20, 30), placed(5, 45, 60)]);
    expect(statuses(r)).toEqual(['verified', 'contradicted', 'verified', 'verified', 'verified']);
    expect(r.s2).toMatchObject({ expected: 'before s3' });
  });
  it('tolerates small ordering jitter', () =>
    expect(statuses(run([placed(1, 0, 10), placed(2, 11, 20), placed(3, 10, 20), placed(4, 20, 30), placed(5, 30, 60)]))).toEqual(Array(5).fill('verified')));
  it('still uses a slightly jittered step as a neighbour when placing the gap', () =>
    expect(run([placed(1, 10, 12), placed(2, 9, 21), placed(4, 21, 30)], [absent(3)]).s3).toMatchObject({ status: 'skipped', between: [21, 21] }));
  it('keeps a contradiction seen on camera rather than replacing it with an ordering one', () => {
    const wrong = { ...placed(2, 40, 45), checkResults: [{ check: 'Step 2 visible', result: 'contradicted' as const, note: 'wrong tube' }] };
    expect(run([placed(1, 0, 10), wrong, placed(3, 10, 20)]).s2).toMatchObject({ status: 'contradicted', observed: 'wrong tube' });
  });
  it('ignores annotation-assisted observations', () => {
    const fixture = (n: number, s: number, e: number) => ({ ...placed(n, s, e), provenance: 'annotation_assisted_visual_review' as const });
    expect(run([fixture(1, 30, 40), fixture(2, 0, 10)]).s1.status).toBe('verified');
  });
});

describe('Core and detail checks', () => {
  const step: ProtocolRequirement = { id: 'p', order: 1, title: 'Return plate', description: 'Place the plate back in the incubator.', criticality: 'important' as const, category: 'action' as const,
    checks: ['Plate is placed inside the incubator', 'The incubator door is closed afterwards'], details: ['The incubator door is closed afterwards'] };
  const seen = (results: ('confirmed' | 'contradicted' | 'not_visible')[], confidence = .7): Observation => ({
    ...placed(1, 0, 10, confidence), stepId: 'p', checkResults: step.checks!.map((check, i) => ({ check, result: results[i], note: '' }))
  });
  const verdict = (o: Observation, s = step) => verifyRun({ ...method, requirements: [s] }, { observations: [o], duration: 60 }).p;
  it('verifies a done step and flags the unseen detail, without a separate status', () =>
    expect(verdict(seen(['confirmed', 'not_visible']))).toMatchObject({ status: 'verified', unconfirmedDetails: ['The incubator door is closed afterwards'] }));
  it('leaves no flag when every detail is seen', () =>
    expect(verdict(seen(['confirmed', 'confirmed']))).not.toHaveProperty('unconfirmedDetails'));
  it('stays unverifiable when the core action is not seen', () =>
    expect(verdict(seen(['not_visible', 'confirmed']))).toMatchObject({ status: 'unverifiable', missingEvidence: ['Plate is placed inside the incubator'] }));
  it('still contradicts on a clearly broken detail', () =>
    expect(verdict(seen(['confirmed', 'contradicted'], .9)).status).toBe('contradicted'));
  it('treats every check as core when the contract marks no details', () =>
    expect(verdict(seen(['confirmed', 'not_visible']), { ...step, details: undefined }).status).toBe('unverifiable'));
});
