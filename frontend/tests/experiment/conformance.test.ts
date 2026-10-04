import { describe, expect, it } from 'vitest';
import { evidenceDebt, verify, verifyMethod } from '../../src/lib/experiment/conformance';
import { canonicalize, sha256 } from '../../src/lib/experiment/hashing';
import { demoMethod } from '../../src/lib/experiment/demo';
import fixtures from '../../src/lib/experiment/fixtures/observations.json';
import { compileMethod, compileProtocolText } from '../../src/lib/experiment/protocol';
import { createRecord, validateRecord } from '../../src/lib/experiment/record';
import type { Observation, ProtocolRequirement, Run } from '../../src/lib/experiment/types';
const dji16: Run = { id: 'DJI_16', title: 'CRISPR delivery', subtitle: 'Documented deviation', date: '2025-08-01', video: '/demo/DJI_16.mp4', duration: 25.3, observations: fixtures.DJI_16 as Observation[] };
const requirement: ProtocolRequirement = { id: 'transfer', title: 'Transfer', description: 'Transfer to tube A', order: 1, action: 'pipette_transfer', target: 'tube_A', requiredEvidence: ['action', 'target'], criticality: 'critical', category: 'identity' };
const observation: Observation = { id: 'obs', stepId: 'transfer', timestampStart: 10, timestampEnd: 20, observed: { action: 'pipette_transfer', target: 'tube_A' }, establishes: ['action', 'target'], evidence: [{ id: 'frame', timestamp: 15, end: 20, kind: 'video', description: 'Transfer to labelled tube A' }], uncertain: [], confidence: .95, provenance: 'claude_agent' };
describe('Conformance: evidence before verdict', () => {
  it('verifies an observed transfer with the required evidence', () => expect(verify(requirement, [observation]).status).toBe('verified'));
  it('contradicts an established wrong target', () => expect(verify(requirement, [{ ...observation, observed: { ...observation.observed, target: 'tube_B' } }]).status).toBe('contradicted'));
  it('abstains when required evidence is missing', () => expect(verify({ ...requirement, requiredEvidence: ['volume_display'] }, [observation])).toMatchObject({ status: 'unverifiable', missingEvidence: ['volume_display'] }));
  it('does not accuse based on an unestablished target guess', () => expect(verify(requirement, [{ ...observation, observed: { target: 'tube_B', action: 'pipette_transfer' }, establishes: ['action'] }]).status).toBe('unverifiable'));
  it('contradicts directly observed invalid ordering', () => expect(verify({ ...requirement, after: ['prior'] }, [observation, { ...observation, id: 'prior-obs', stepId: 'prior', timestampStart: 25, timestampEnd: 30 }]).status).toBe('contradicted'));
  it('abstains when an ordering anchor is missing', () => expect(verify({ ...requirement, after: ['prior'] }, [observation]).status).toBe('unverifiable'));
  it('verifies timing inside allowed tolerance, including boundaries', () => {
    for (const duration of [29, 30, 31]) expect(verify({ ...requirement, duration: { expectedSeconds: 30, minSeconds: 29, maxSeconds: 31 } }, [{ ...observation, observed: { ...observation.observed, durationSeconds: duration }, establishes: [...observation.establishes, 'duration'] }]).status).toBe('verified');
  });
  it('contradicts continuous timing outside tolerance', () => expect(verify({ ...requirement, duration: { expectedSeconds: 30, minSeconds: 29, maxSeconds: 31 } }, [{ ...observation, observed: { ...observation.observed, durationSeconds: 28 }, establishes: [...observation.establishes, 'duration'] }]).status).toBe('contradicted'));
  it('does not derive elapsed duration from two sampled frames', () => expect(verify({ ...requirement, duration: { expectedSeconds: 10 } }, [observation]).status).toBe('unverifiable'));
  it('never verifies a low-confidence observation', () => expect(verify(requirement, [{ ...observation, confidence: .79 }]).status).toBe('unverifiable'));
  it('never contradicts a low-confidence observation', () => expect(verify(requirement, [{ ...observation, confidence: .3, observed: { target: 'wrong' } }]).status).toBe('unverifiable'));
  it('requires timestamped evidence even for matching fields', () => expect(verify(requirement, [{ ...observation, evidence: [] }]).status).toBe('unverifiable'));
  it('does not treat missing footage as a skipped step', () => expect(verify(requirement, []).status).toBe('unverifiable'));
  it('counts unresolved requirements but not contradictions as evidence debt', () => {
    const m = { ...demoMethod, requirements: [requirement, { ...requirement, id: 'missing', criticality: 'informational' as const }] };
    const results = verifyMethod(m, [{ ...observation, observed: { ...observation.observed, target: 'wrong' } }]);
    expect(evidenceDebt(m, results)).toMatchObject({ percent: 25, totalWeight: 4, unresolvedWeight: 1 });
  });
});
describe('Agent check results', () => {
  const step: ProtocolRequirement = { id: 'add', title: 'Add reagent 2', description: 'Add reagent 2 into the EP tube.', order: 1, checks: ['Pipette tip enters the EP tube', 'Reagent 2 is the source'], criticality: 'critical', category: 'identity' };
  const agent = (results: ('confirmed' | 'contradicted' | 'not_visible')[], confidence = .9): Observation => ({ ...observation, stepId: 'add', observed: {}, establishes: [], confidence, summary: 'Transfer visible', checkResults: step.checks!.map((check, i) => ({ check, result: results[i], note: `note ${i}` })) });
  it('verifies only when every check is confirmed', () => expect(verify(step, [agent(['confirmed', 'confirmed'])]).status).toBe('verified'));
  it('contradicts on a clearly contradicted check and reports it', () => expect(verify(step, [agent(['contradicted', 'confirmed'])])).toMatchObject({ status: 'contradicted', expected: 'Pipette tip enters the EP tube', observed: 'note 0' }));
  it('abstains when a check is not visible', () => expect(verify(step, [agent(['confirmed', 'not_visible'])])).toMatchObject({ status: 'unverifiable', missingEvidence: ['Reagent 2 is the source'] }));
  it('does not let a low-confidence agent contradict', () => expect(verify(step, [agent(['contradicted', 'confirmed'], .6)]).status).toBe('unverifiable'));
  it('abstains when the step was not located', () => expect(verify(step, []).status).toBe('unverifiable'));
});
describe('Method & record integrity', () => {
  it('compiles real numbered LSV methodology into its execution contract', () => expect(compileProtocolText(demoMethod.requirements.map((r, i) => `${i + 1}. ${r.description}`).join('\n'))).toEqual(demoMethod));
  it('canonical method hashing is stable across object key order', async () => expect(await sha256({ b: { y: 2, x: 1 }, a: [2, 1] })).toBe(await sha256({ a: [2, 1], b: { x: 1, y: 2 } })));
  it('preserves array order as meaningful protocol data', async () => expect(await sha256([1, 2])).not.toBe(await sha256([2, 1])));
  it('rejects non-finite values instead of silently changing them', () => expect(() => canonicalize({ confidence: NaN })).toThrow());
  it('detects tampered observations and record payloads', async () => {
    const record = await createRecord(dji16, demoMethod);
    expect(await validateRecord(record)).toBe(true); record.run = structuredClone(record.run); record.run.observations[0].observed.target = 'tampered'; expect(await validateRecord(record)).toBe(false);
  });
  it('detects a tampered method', async () => {
    const record = await createRecord(dji16, demoMethod); record.method = structuredClone(record.method); record.method.requirements[0].description = 'changed';
    expect(await validateRecord(record)).toBe(false);
  });
  it('rejects duplicate identifiers and dangling ordering anchors', () => {
    const m = structuredClone(demoMethod); m.requirements[1].id = m.requirements[0].id; expect(() => compileMethod(JSON.stringify(m))).toThrow('unique');
    m.requirements[1].id = 'restored'; m.requirements[3].after = ['absent']; expect(() => compileMethod(JSON.stringify(m))).toThrow('Invalid ordering');
  });
  it('rejects requirements with nothing to check', () => expect(() => compileMethod(JSON.stringify({ ...demoMethod, requirements: [{ id: 'x', title: 'Empty', description: '', order: 1, criticality: 'important', category: 'action' }] }))).toThrow('predicate'));
});
describe('Annotation-assisted LSV fixture', () => {
  it('finds the labelled wrong target and abstains on missing incubation', () => {
    const result = verifyMethod(demoMethod, dji16.observations); expect(result['step-02']).toMatchObject({ status: 'contradicted', expected: { target: 'mixing_tube' }, observed: { target: 'reagent_3' } }); expect(result['step-05'].status).toBe('unverifiable');
  });
});
