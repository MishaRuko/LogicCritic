import { describe, expect, it } from 'vitest';
import { adjacentGroups, runAgent, unverifiableReason, toContract, type AgentCall } from '../../src/lib/experiment/agent';
import { verifyMethod } from '../../src/lib/experiment/conformance';
const method = toContract({ title: 'Mock', summary: '', steps: [
  { title: 'Add reagent', description: 'Add reagent 1 to the tube.', defining_action: '', visual_group: '', checks: ['Tip enters the tube'], detail_checks: [], caveats: ['Sterility'], criticality: 'critical', category: 'action' },
  { title: 'Incubate', description: 'Incubate 20 min.', defining_action: '', visual_group: '', checks: ['Tube left for 20 minutes'], detail_checks: [], caveats: [], criticality: 'important', category: 'timing' }
] }, 'mock.pdf');
const findings = (steps: string[]) => ({ steps: steps.map(id => ({ step_id: id, found: id === 'step-01', absence: id === 'step-01' ? 'n/a' : 'not_performed', start_seconds: id === 'step-01' ? 2 : 0, end_seconds: id === 'step-01' ? 6 : 0, summary: 'Tip enters the tube.', check_results: [{ check_index: 0, result: 'confirmed', note: 'Visible at 4 s' }], evidence: [{ seconds: 4, description: 'Tip in tube' }], uncertainties: [], confidence: .9 })) });
function scripted(turns: unknown[][]): { call: AgentCall; requests: unknown[][] } {
  const requests: unknown[][] = []; let i = 0;
  return { requests, call: async messages => { requests.push(structuredClone(messages)); const content = turns[i++] as never; return { content, stop_reason: 'tool_use' }; } };
}
describe('Video agent loop', () => {
  it('supplies requested frames, then turns findings into observations the rules can judge', async () => {
    const { call, requests } = scripted([
      [{ type: 'tool_use', id: 't1', name: 'view_frames', input: { start_seconds: 1, end_seconds: 7, count: 3 } }],
      [{ type: 'tool_use', id: 't2', name: 'submit_findings', input: findings(['step-01', 'step-02']) }]
    ]);
    const asked: number[][] = [];
    const { observations, absences } = await runAgent({ method, duration: 30, overview: [{ t: 1, data: 'AAAA' }], call, getFrames: async (s, e, c) => { asked.push([s, e, c]); return [{ t: s, data: 'BBBB' }]; } });
    expect(asked).toEqual([[1, 7, 3]]);
    expect(JSON.stringify(requests[1])).toContain('BBBB');
    expect(observations).toHaveLength(1);
    const results = verifyMethod(method, observations);
    expect(results['step-01'].status).toBe('verified');
    expect(results['step-02'].status).toBe('unverifiable');
    expect(absences).toEqual([{ stepId: 'step-02', reason: 'not_performed', confidence: .9, note: 'Tip enters the tube.' }]);
  });
  it('rejects findings that skip a step and asks again', async () => {
    const { call } = scripted([
      [{ type: 'tool_use', id: 't1', name: 'submit_findings', input: findings(['step-01']) }],
      [{ type: 'tool_use', id: 't2', name: 'submit_findings', input: findings(['step-01', 'step-02']) }]
    ]);
    const events: string[] = [];
    await runAgent({ method, duration: 30, overview: [], call, getFrames: async () => [], onEvent: e => events.push(e.kind) });
    expect(events).toContain('retry');
  });
  it('enforces the frame budget', async () => {
    const { call } = scripted([
      [{ type: 'tool_use', id: 't1', name: 'view_frames', input: { start_seconds: 0, end_seconds: 10, count: 8 } }],
      [{ type: 'tool_use', id: 't2', name: 'submit_findings', input: findings(['step-01', 'step-02']) }]
    ]);
    const counts: number[] = [];
    await runAgent({ method, duration: 30, overview: [], call, frameBudget: 5, getFrames: async (_s, _e, c) => { counts.push(c); return []; } });
    expect(counts).toEqual([5]);
  });
});

describe('Checks video cannot clearly establish', () => {
  it('moves them to the caveats, with the reason', () => {
    for (const [c, reason] of [
      ['Pipette volume reading of about 1 ml if visible', 'Measured quantity'], ['Pipette graduation reading of about 3 ml is visible', 'Measured quantity'],
      ['Final liquid level is consistent with about 10 ml in a 10 cm dish', 'Measured quantity'], ['5 µL of plasmid is drawn up', 'Measured quantity'],
      ['Pipette draws from the container labeled reagent 1', 'Label or contents'], ['The tube label is legible', 'Label or contents'],
      ['About 4-5 distinct flicks are visible', 'Exact count'], ['The suspension is pipetted 10 times', 'Exact count'],
      ['The tube stays on ice for about 5 seconds', 'Exact duration or temperature'], ['Tube left undisturbed for about 20 minutes', 'Exact duration or temperature'],
      ['The display shows 42°C', 'Exact duration or temperature'], ['Each bottle or item is sprayed before it enters the cabinet', 'Claim about every item'],
      ['All reagents are wiped down', 'Claim about every item'], ['The tube is in the water bath for exactly the stated time', 'Exact duration or temperature']
    ]) expect(unverifiableReason(c), c).toBe(reason);
  });
  it('keeps actions, vessels recognised by appearance, order and qualitative timing', () => {
    for (const c of ['A fresh 1.5 mL EP tube is opened', 'Reagent 1 is dispensed into the 1.5 mL EP tube', 'The receiving vessel is the 10 cm dish',
      'Tube size used (15 ml or 50 ml) is noted', 'The tube is put into the ice and left there', 'The tube is flicked with a finger',
      'This happens after the heat shock', 'The plate is turned upside down', 'The tube is moved to the thermal cycler',
      'Items are cleaned, by spraying or wiping, before entering the hood'])
      expect(unverifiableReason(c), c).toBeUndefined();
  });
  it('rewrites the contract so only clearly visible checks reach the agent', () => {
    const m = toContract({ title: 'T', summary: '', steps: [{ title: 'Add', description: 'Add 5 µL.', defining_action: '', visual_group: '', checks: ['Tip enters the tube', 'Pipette volume reads 5 µL'], detail_checks: [], caveats: [], criticality: 'critical', category: 'action' }] }, 'x');
    expect(m.requirements[0].checks).toEqual(['Tip enters the tube']);
    expect(m.requirements[0].caveats).toEqual(['Measured quantity, not verifiable from video: Pipette volume reads 5 µL']);
  });
  it('keeps detail checks separate, after the core ones, and filters them too', () => {
    const m = toContract({ title: 'T', summary: '', steps: [{ title: 'Return plate', description: 'x', defining_action: '', visual_group: '', checks: ['Plate goes into the incubator'], detail_checks: ['Door is closed afterwards', 'Plate is held for about 5 seconds'], caveats: [], criticality: 'important', category: 'action' }] }, 'x');
    expect(m.requirements[0].checks).toEqual(['Plate goes into the incubator', 'Door is closed afterwards']);
    expect(m.requirements[0].details).toEqual(['Door is closed afterwards']);
    expect(m.requirements[0].caveats).toEqual(['Exact duration or temperature, not verifiable from video: Plate is held for about 5 seconds']);
  });
  it('keeps look-alike groups only for consecutive steps', () => {
    expect(adjacentGroups(['', 'ice', 'heat', 'ice', ''])).toEqual([undefined, undefined, undefined, undefined, undefined]);
    expect(adjacentGroups(['add', 'add', 'add', 'mix'])).toEqual(['add', 'add', 'add', undefined]);
    expect(adjacentGroups(['add', 'add', 'mix', 'add', 'add', ' '])).toEqual(['add', 'add', undefined, 'add (2)', 'add (2)', undefined]);
    expect(adjacentGroups(['spray', 'gather', 'spray'])).toEqual([undefined, undefined, undefined]);
  });
  it('carries the defining action and look-alike group into the contract, dropping empty ones', () => {
    const step = (title: string, group: string) => ({ title, description: title, defining_action: `${title} action`, visual_group: group, checks: ['Tip enters the tube'], detail_checks: [], caveats: [], criticality: 'critical' as const, category: 'action' as const });
    const m = toContract({ title: 'T', summary: '', steps: [step('Add reagent 1', 'reagent addition'), step('Add reagent 2', 'reagent addition'), step('Mix', ' ')] }, 'x');
    expect(m.requirements.map(r => r.visualGroup)).toEqual(['reagent addition', 'reagent addition', undefined]);
    expect(m.requirements[2].definingAction).toBe('Mix action');
  });
});
