import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/lib/research/api';
import { LiveCamera } from '../src/components/experiment/LiveCamera';
import { LiveSessionPanel } from '../src/components/experiment/LiveSessionPanel';
import { ResearchExperiments } from '../src/components/research/ResearchExperiments';
import type { Experiments, ExperimentProtocol, ExperimentRun, Snapshot } from '../src/types/api';

vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn(async () => 'data:image/png;base64,qr') },
}));
afterEach(() => vi.restoreAllMocks());

const protocol = {
  id: 'protocol',
  source_id: 'source',
  protocol: {
    id: 'protocol',
    title: 'Splitting cells',
    version: '1',
    steps: [
      { id: 's1', description: 'Aspirate the old medium.', checks: [] },
      { id: 's2', description: 'Add 3 ml PBS.', checks: [] },
    ],
  },
  step_excerpts: {},
  current: true,
} as unknown as ExperimentProtocol;

const liveRun = (result: Partial<ExperimentRun['result']> = {}): ExperimentRun =>
  ({
    id: 'live-run',
    protocol_id: 'protocol',
    mode: 'live',
    status: 'running',
    filename: 'Live session',
    result: { live: { observations: [], deviations: [] }, ...result },
    error: null,
    created_at: '2026-10-08T10:00:00Z',
    completed_at: null,
  }) as ExperimentRun;

describe('live session dashboard', () => {
  it('offers the camera link as a QR code and waits for the phone', async () => {
    render(<LiveSessionPanel run={liveRun()} protocol={protocol} />);
    expect(await screen.findByAltText(/QR code that opens the camera page/)).toHaveAttribute(
      'src',
      'data:image/png;base64,qr',
    );
    expect(screen.getByRole('link', { name: /Use this device/ })).toHaveAttribute(
      'href',
      `${window.location.origin}/live/live-run`,
    );
    expect(screen.getByText('Waiting for the camera…')).toBeInTheDocument();
  });

  it('shows each step as it is recognised, and deviations as they happen', () => {
    const run = liveRun({
      processed_seconds: 40,
      live: {
        observations: [
          {
            id: 'o1',
            step_id: 's1',
            status: 'performed',
            confidence: 0.9,
            description: 'Medium removed with the aspirator.',
            values: [],
            span: { start_s: 12, end_s: 20 },
          },
        ],
        deviations: [
          {
            id: 'd1',
            kind: 'wrong_value',
            step_id: 's2',
            check_id: null,
            message: 'About 5 ml PBS was added instead of 3 ml.',
            needs_review: true,
            expected: 3,
            observed: 5,
            span: { start_s: 31, end_s: 40 },
          },
        ],
      },
    });
    render(<LiveSessionPanel run={run} protocol={protocol} />);
    expect(screen.getByText(/Analysed up to 0:40/)).toBeInTheDocument();
    expect(screen.getByText(/performed at 0:12 · Medium removed/)).toBeInTheDocument();
    expect(
      screen.getByText('0:31 · About 5 ml PBS was added instead of 3 ml.'),
    ).toBeInTheDocument();
  });

  it('stops the session from the dashboard', async () => {
    const stop = vi.spyOn(api, 'stopLiveSession').mockResolvedValue(liveRun());
    render(<LiveSessionPanel run={liveRun()} protocol={protocol} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }));
    await waitFor(() => expect(stop).toHaveBeenCalledWith('live-run'));
    expect(screen.getByRole('button', { name: 'Finishing…' })).toBeDisabled();
  });

  it('starts a live session from the experiment setup', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue(liveRun());
    const state = {
      workspace: { id: 'workspace' },
      sources: [
        { id: 'source', title: 'Splitting cells', original_filename: 'p.md', excerpts: [] },
      ],
      jobs: [],
    } as unknown as Snapshot;
    const data: Experiments = { verified: true, protocols: [protocol], runs: [] };
    render(
      <ResearchExperiments
        state={state}
        data={data}
        loading={false}
        error={null}
        busy={false}
        perform={async action => action()}
        onSource={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Start live session' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        'protocol',
        'live',
        undefined,
        'source',
        false,
      ),
    );
  });
});

describe('live camera page', () => {
  it('refuses a session that has ended', async () => {
    vi.spyOn(api, 'fetchExperimentRun').mockResolvedValue({ ...liveRun(), status: 'succeeded' });
    render(<LiveCamera runId="live-run" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('already ended');
  });

  it('asks before using the camera, and explains a refusal', async () => {
    vi.spyOn(api, 'fetchExperimentRun').mockResolvedValue(liveRun());
    const getUserMedia = vi.fn(async () => {
      throw new DOMException('denied', 'NotAllowedError');
    });
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia },
      configurable: true,
    });
    render(<LiveCamera runId="live-run" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Start camera' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Camera access was refused');
    expect(getUserMedia).toHaveBeenCalledWith(
      expect.objectContaining({ audio: false, video: expect.anything() }),
    );
  });
});
