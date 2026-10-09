import { describe, expect, it } from 'vitest';
import { forVariant, protocolVariants } from '../src/lib/experiment/procedures';
import type { ExperimentProtocol } from '../src/types/api';

const step = (id: string, variant?: string) => ({
  id,
  description: id,
  source_text: id,
  optional: false,
  checks: [],
  variant,
});
const protocol = {
  id: 'p',
  source_id: 's',
  protocol: {
    id: 'p',
    title: 'Two devices',
    version: '1',
    steps: [step('s1'), step('s2', 'Device A'), step('s3', 'Device B'), step('s4')],
  },
  step_excerpts: {},
  extraction_method: 'lab_vision_model',
  approved_at: null,
  created_at: '',
} as ExperimentProtocol;

describe('procedures a source describes', () => {
  it('lists them and keeps one, with the steps they share', () => {
    expect(protocolVariants(protocol)).toEqual(['Device A', 'Device B']);
    const ids = (variant?: string) => forVariant(protocol, variant).protocol.steps.map(s => s.id);
    expect(ids('Device B')).toEqual(['s1', 's3', 's4']);
    expect(ids()).toEqual(['s1', 's2', 's4']); // the first by default
    expect(ids('Device C')).toEqual(['s1', 's2', 's4']);
  });
  it('leaves a single procedure as it is', () => {
    const single = { ...protocol, protocol: { ...protocol.protocol, steps: [step('s1')] } };
    expect(protocolVariants(single)).toEqual([]);
    expect(forVariant(single, 'anything')).toBe(single);
    expect(forVariant(undefined)).toBeUndefined();
  });
});
