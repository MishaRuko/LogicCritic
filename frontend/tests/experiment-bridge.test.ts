import { describe, expect, it } from 'vitest';
import { experimentPresentation } from '../src/lib/experiment/bridge';
import { coverage } from '../src/lib/experiment/playback';
import type { ExperimentProtocol, ExperimentRun } from '../src/types/api';

const protocol = {
  id: 'p',
  source_id: 'source',
  protocol: {
    title: 'Procedure',
    version: '1',
    steps: [
      {
        id: 's1',
        description: 'Add 0.25% reagent.',
        source_text: 'Add 0.25% reagent.',
        checks: [],
      },
      { id: 's2', description: 'Mix gently.', source_text: 'Mix gently.', checks: [] },
    ],
  },
} as unknown as ExperimentProtocol;
const observation = {
  id: 'o',
  step_id: 's1',
  status: 'performed',
  confidence: 0.9,
  span: { start_s: 0, end_s: 6 },
  values: [],
  description: 'Reagent added.',
};
const job = {
  id: 'r',
  created_at: '2026-10-04',
  filename: 'short.mp4',
  mode: 'video',
  result: { coverage: 'excerpt', observations: [observation], deviations: [] },
} as unknown as ExperimentRun;

describe('experiment presentation', () => {
  it('preserves decimal concentrations and partial coverage without claiming later steps were skipped', () => {
    const { method, results } = experimentPresentation(job, protocol);
    expect(method.requirements[0].title).toBe('Add 0.25% reagent.');
    expect(results.s1.status).toBe('verified');
    expect(results.s2).toMatchObject({
      status: 'unverifiable',
      reason: 'Outside the recorded excerpt; this step was not assessed.',
    });
  });

  it('keeps a completed action unverifiable when its setting needs review', () => {
    const issue = {
      id: 'd',
      step_id: 's1',
      kind: 'unverified_check',
      message: 'Concentration unreadable.',
      needs_review: true,
    };
    const result = experimentPresentation(
      { ...job, result: { ...job.result, deviations: [issue as never] } },
      protocol,
    ).results;
    expect(result.s1).toMatchObject({
      status: 'unverifiable',
      reason: 'Concentration unreadable.',
    });
  });

  it('retains supported contradictions and flattens overlapping windows using the strongest finding', () => {
    const issue = {
      id: 'd',
      step_id: 's1',
      kind: 'wrong_value',
      message: 'Wrong setting.',
      needs_review: false,
      expected: 5,
      observed: 10,
    };
    const { run, results } = experimentPresentation(
      { ...job, result: { ...job.result, deviations: [issue as never] } },
      protocol,
    );
    expect(results.s1).toMatchObject({ status: 'contradicted', expected: 5, observed: 10 });
    expect(coverage(run.observations, results, 10).seconds).toMatchObject({
      contradicted: 6,
      none: 4,
    });
  });
});

it('presents the experiment branch agent checks and persisted verdicts without changing them', () => {
  const agentMethod = {
    schemaVersion: 1,
    title: 'Procedure',
    version: '1',
    source: 'source.md',
    requirements: [
      {
        id: 's1',
        order: 1,
        title: 'Add',
        description: 'Add 0.25% reagent.',
        quote: 'Add 0.25% reagent.',
        checks: ['Tip enters the tube'],
        caveats: ['Concentration is not visible'],
        criticality: 'important',
        category: 'action',
      },
    ],
  } as const;
  const agentObservation = {
    id: 'agent-o',
    stepId: 's1',
    timestampStart: 2,
    timestampEnd: 5,
    confidence: 0.9,
    observed: {},
    establishes: [],
    uncertain: [],
    evidence: [],
    provenance: 'claude_agent',
  };
  const agentResults = { s1: { status: 'verified', confidence: 0.9, evidence: [] } };
  const agentJob = {
    ...job,
    result: {
      ...job.result,
      duration: 30,
      agent_method: agentMethod,
      agent_observations: [agentObservation],
      agent_results: agentResults,
    },
  } as unknown as ExperimentRun;
  const presentation = experimentPresentation(agentJob, protocol);
  expect(presentation.method).toBe(agentMethod);
  expect(presentation.run.observations).toBe(agentJob.result.agent_observations);
  expect(presentation.results).toBe(agentJob.result.agent_results);
  expect(presentation.run.video).toBe('/api/experiment-runs/r/recording');
  expect(presentation.run.duration).toBe(30);
});
