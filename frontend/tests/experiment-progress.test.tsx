import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { VideoAnalysisProgress } from '../src/components/experiment/VideoAnalysisProgress';
import type { ExperimentRun } from '../src/types/api';

const job: ExperimentRun = { id: 'run', protocol_id: 'protocol', mode: 'video', status: 'running', filename: 'recording.mp4', result: {}, error: null, created_at: '2026-10-04T10:00:00Z', completed_at: null };

describe('experiment pipeline progress', () => {
  it('shows actual sampled frames and inspection requests while keeping verification pending', () => {
    render(<VideoAnalysisProgress job={{ ...job, result: { analysis_stage: 'inspection', duration: 30, overview: [{ t: 1.5, data: 'jpeg' }], agent_events: [{ kind: 'inspect', start: 0, end: 6, count: 6 }] } }}/>);
    expect(screen.getByRole('img', { name: 'Recording at 00:01' })).toHaveAttribute('src', 'data:image/jpeg;base64,jpeg');
    expect(screen.getByText('Inspect 00:00–00:06 · 6 frames')).toBeInTheDocument();
    expect(screen.getByText('Inspect with the video agent').closest('li')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('Check verifiability').closest('li')).not.toHaveAttribute('aria-current');
    expect(screen.getByRole('progressbar', { name: 'Experiment analysis progress' })).toHaveAttribute('aria-valuenow', '2');
  });

  it('clearly identifies saved replay without suggesting live agent work', () => {
    render(<VideoAnalysisProgress job={{ ...job, mode: 'replay' }}/>);
    expect(screen.getByText('Replay saved observations. No new frames or agents are generated.')).toBeInTheDocument();
    expect(screen.queryByText('Inspect with the video agent')).not.toBeInTheDocument();
  });

  it('retains elapsed time and unresolved verdicts after completion', () => {
    render(<VideoAnalysisProgress job={{ ...job, status: 'succeeded', completed_at: '2026-10-04T10:01:32Z', result: { agent_results: { s1: { status: 'verified', confidence: .9, evidence: [] }, s2: { status: 'unverifiable', reason: 'Unreadable label', missingEvidence: ['label'], evidence: [] } } } }}/>);
    expect(screen.getByText('Completed in 01:32')).toBeInTheDocument();
    expect(screen.getByText(/1 verified · 0 contradicted · 1 unverifiable/)).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '4');
  });
});
