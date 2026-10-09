import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import * as api from '../src/lib/research/api';
import { ResearchExperiments } from '../src/components/research/ResearchExperiments';
import type { Experiments, ExperimentProtocol, ExperimentRun, Snapshot } from '../src/types/api';

const source = {
  id: 'source',
  title: 'Bench study',
  original_filename: 'study.md',
  origin: 'upload',
  metadata: {},
  excerpts: [
    {
      id: 'methods-1',
      text: 'Set the pipette to 50 uL.',
      locator: { section: 'Methods' },
      sequence: 1,
    },
  ],
};
const state = {
  workspace: { id: 'workspace' },
  sources: [source],
  jobs: [],
} as unknown as Snapshot;
const protocol: ExperimentProtocol = {
  id: 'protocol',
  source_id: 'source',
  protocol: {
    id: 'protocol',
    title: 'Bench study',
    version: '1',
    steps: [
      {
        id: 's1',
        description: 'Set the pipette to 50 uL.',
        source_text: 'Set the pipette to 50 uL.',
        optional: false,
        checks: [],
      },
    ],
  },
  step_excerpts: { s1: ['excerpt'] },
  extraction_method: 'numbered_instructions',
  approved_at: null,
  current: true,
  created_at: '2026-10-04',
};
const data: Experiments = { verified: true, protocols: [protocol], runs: [] };
const perform = async (action: () => Promise<void>) => action();
const props = {
  state,
  loading: false,
  error: null,
  busy: false,
  perform,
  onVerify: vi.fn(),
  onSource: vi.fn(),
};
beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:preview');
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.restoreAllMocks());

describe('research to experiment handoff', () => {
  const paper = { ...source, id: 'paper', title: 'A paper', origin: 'amass' };
  const handedOver = {
    ...source,
    id: 'handed-over',
    title: 'Protocol: heat shock',
    origin: 'agent',
  };
  const agentSource = (id: string, created_at: string) => ({
    ...source,
    id,
    title: `Protocol: ${id}`,
    origin: 'agent',
    created_at,
    metadata: { parser: 'agent_protocol_v1' },
  });
  const withSources = (...sources: object[]) => ({ ...state, sources }) as unknown as Snapshot;
  const suggested = (source_id: string) => ({
    run_id: 'run',
    source_id,
    protocol_id: 'prepared',
    current: true,
  });

  it('defaults to the protocol the latest research run handed over, not the first source', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'run' } as never);
    render(
      <ResearchExperiments
        {...props}
        state={withSources(paper, handedOver)}
        data={{ ...data, protocols: [], suggested: suggested('handed-over') }}
      />,
    );
    expect(screen.getByText('Compiled by the research agent')).toBeInTheDocument();
    expect(screen.queryByLabelText('Methods from one paper')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run sample experiment' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        undefined,
        'demo',
        undefined,
        'handed-over',
        false,
      ),
    );
  });

  it('lets a person choose a different source than the suggested protocol', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'run' } as never);
    render(
      <ResearchExperiments
        {...props}
        state={withSources(paper, handedOver)}
        data={{ ...data, protocols: [], suggested: suggested('handed-over') }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: "Use one paper's methods instead" }));
    fireEvent.change(screen.getByLabelText('Methods from one paper'), {
      target: { value: 'paper' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Run sample experiment' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith('workspace', undefined, 'demo', undefined, 'paper', false),
    );
  });

  it('previews the agent protocol and uses its prepared version when older experiments exist', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'new-run' } as never);
    const research = {
      ...paper,
      excerpts: [{ id: 'evidence', text: 'Set the pipette to 50 uL.', source_id: 'paper' }],
    };
    const handedOver = {
      ...agentSource('handed-over', '2026-10-05T11:00:00Z'),
      metadata: {
        parser: 'agent_protocol_v1',
        basis: 'Follows the paper method.',
        steps: [{ n: 1, action: 'Set the pipette to 50 uL.', excerpt_ids: ['evidence'] }],
      },
    };
    const prepared = { ...protocol, id: 'prepared', source_id: 'handed-over' };
    const oldRun = {
      id: 'old-run',
      protocol_id: protocol.id,
      status: 'succeeded',
      filename: 'old.mp4',
      created_at: '2026-10-04',
      result: {},
    } as ExperimentRun;
    render(
      <ResearchExperiments
        {...props}
        state={withSources(research, handedOver)}
        data={{
          ...data,
          protocols: [prepared, protocol],
          runs: [oldRun],
          suggested: suggested('handed-over'),
        }}
      />,
    );
    expect(screen.getByRole('region', { name: 'Selected protocol' })).toHaveTextContent(
      'Written by the research agent',
    );
    expect(screen.getByText('Follows the paper method.')).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Review the \d+ steps/));
    fireEvent.click(screen.getByText('Research evidence · 1 passage'));
    fireEvent.click(screen.getByRole('button', { name: 'A paper' }));
    expect(props.onSource).toHaveBeenCalledWith('paper');
    fireEvent.click(screen.getByRole('button', { name: 'Run sample experiment' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        'prepared',
        'demo',
        undefined,
        'handed-over',
        false,
      ),
    );
  });

  it('uses the uploaded video with the prepared agent protocol', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'run' } as never);
    const prepared = { ...protocol, id: 'prepared', source_id: 'handed-over' };
    render(
      <ResearchExperiments
        {...props}
        state={withSources(paper, agentSource('handed-over', '2026-10-05T11:00:00Z'))}
        data={{ ...data, protocols: [prepared], suggested: suggested('handed-over') }}
      />,
    );
    const video = new File(['recording'], 'bench.mp4', { type: 'video/mp4' });
    fireEvent.change(screen.getByLabelText('Choose a lab video or saved observations'), {
      target: { files: [video] },
    });
    expect(screen.getByLabelText('Lab video preview')).toHaveAttribute('src', 'blob:preview');
    fireEvent.click(screen.getByRole('button', { name: 'Run experiment analysis' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        'prepared',
        'video',
        video,
        'handed-over',
        false,
      ),
    );
  });

  it('keeps an old run available without making it the result for a new protocol', () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    );
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true, json: async () => [] } as Response);
    Element.prototype.scrollTo = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const oldRun = {
      id: 'old-run',
      protocol_id: protocol.id,
      mode: 'demo',
      status: 'succeeded',
      filename: 'old.mp4',
      created_at: '2026-10-04',
      result: {},
    } as ExperimentRun;
    render(
      <QueryClientProvider client={client}>
        <ResearchExperiments
          {...props}
          state={withSources(source, agentSource('newer', '2026-10-05T12:00:00Z'))}
          data={{ ...data, runs: [oldRun], suggested: suggested('newer') }}
        />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole('region', { name: 'Experiment results' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /old.mp4/ }));
    expect(screen.getByRole('region', { name: 'Experiment results' })).toHaveTextContent(
      'Bench study',
    );
    fireEvent.click(screen.getByRole('button', { name: 'New experiment' }));
    expect(screen.getByText('Compiled by the research agent')).toBeInTheDocument();
  });

  it('compiles a methodology from all sources on request, for the last research question', async () => {
    const run = vi.spyOn(api, 'startAgentRun').mockResolvedValue({ id: 'run' } as never);
    const started = vi.fn();
    render(
      <ResearchExperiments
        {...props}
        state={withSources(paper)}
        data={{ ...data, protocols: [], suggested: null }}
        lastQuestion="How is drug X given?"
        onResearchStarted={started}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Compile from all sources' }));
    await waitFor(() => expect(started).toHaveBeenCalled());
    const input = run.mock.calls[0][1];
    expect(input.question).toContain('How is drug X given?');
    expect(input.question).toContain('from the sources already in this workspace');
    expect([input.depth, input.max_web_searches, input.mode]).toEqual(['quick', 0, 'guarded']);
  });

  it('says a methodology is being compiled while research runs', () => {
    render(
      <ResearchExperiments
        {...props}
        state={withSources(paper)}
        data={{ ...data, protocols: [], suggested: null }}
        researching
      />,
    );
    expect(
      screen.queryByRole('button', { name: 'Compile from all sources' }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/a compiled methodology appears here/)).toBeInTheDocument();
  });

  it('asks which procedure is being recorded when the source describes several', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'run' } as never);
    const two = {
      ...protocol,
      source_id: 'paper',
      current: true,
      protocol: {
        ...protocol.protocol,
        steps: [
          { ...protocol.protocol.steps[0], id: 'a1', variant: 'Device A' },
          { ...protocol.protocol.steps[0], id: 'b1', variant: 'Device B' },
        ],
      },
    };
    render(
      <ResearchExperiments
        {...props}
        state={withSources(paper)}
        data={{ ...data, protocols: [two], suggested: null }}
      />,
    );
    const choice = screen.getByLabelText('Procedure to record');
    expect(choice).toHaveValue('Device A');
    fireEvent.change(choice, { target: { value: 'Device B' } });
    fireEvent.click(screen.getByRole('button', { name: 'Run sample experiment' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        two.id,
        'demo',
        undefined,
        'paper',
        false,
        'Device B',
      ),
    );
  });

  it('does not pick an agent protocol the backend is not suggesting', () => {
    // An earlier run handed one over, but the latest run did not: nothing is suggested.
    render(
      <ResearchExperiments
        {...props}
        state={withSources(paper, handedOver)}
        data={{ ...data, protocols: [], suggested: null }}
      />,
    );
    expect(screen.getByLabelText('Methods from one paper')).toHaveValue('paper');
    expect(screen.getByRole('button', { name: 'Compile from all sources' })).toBeInTheDocument();
  });

  it('ignores a suggestion for a source that is not in the workspace', () => {
    render(
      <ResearchExperiments
        {...props}
        state={withSources(paper, handedOver)}
        data={{ ...data, protocols: [], suggested: suggested('missing') }}
      />,
    );
    expect(screen.getByLabelText('Methods from one paper')).toHaveValue('paper');
    expect(screen.getByRole('button', { name: 'Compile from all sources' })).toBeInTheDocument();
  });

  it('opens the requested protocol setup even when a previous result exists', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true, json: async () => [] } as Response);
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    );
    Element.prototype.scrollTo = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const oldRun = {
      id: 'old',
      protocol_id: protocol.id,
      status: 'succeeded',
      mode: 'demo',
      filename: 'old',
      created_at: '2026-10-04',
      result: {},
    } as ExperimentRun;
    render(
      <QueryClientProvider client={client}>
        <ResearchExperiments
          {...props}
          initialSourceId="source"
          data={{ ...data, verified: false, runs: [oldRun] }}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('region', { name: 'Start experiment' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run sample experiment' })).toBeEnabled();
  });

  it('releases selected video previews when replaced and does not preview JSONL', () => {
    render(<ResearchExperiments {...props} data={data} />);
    const input = screen.getByLabelText('Choose a lab video or saved observations');
    const video = new File(['video'], 'video.mp4', { type: 'video/mp4' });
    fireEvent.change(input, { target: { files: [video] } });
    expect(URL.createObjectURL).toHaveBeenCalledWith(video);
    expect(screen.getByLabelText('Lab video preview')).toHaveAttribute('src', 'blob:preview');
    fireEvent.error(screen.getByLabelText('Lab video preview'));
    expect(screen.getByText(/This browser cannot preview/)).toBeInTheDocument();
    fireEvent.change(input, { target: { files: [new File(['{}'], 'observations.jsonl')] } });
    expect(screen.queryByLabelText('Lab video preview')).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview');
  });

  it('runs without an approval checkbox or manual extraction', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'run' } as never);
    render(<ResearchExperiments {...props} data={{ ...data, protocols: [] }} />);
    expect(screen.queryByRole('button', { name: 'Approve protocol' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Extract methodology' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Run sample experiment' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        undefined,
        'demo',
        undefined,
        'source',
        false,
      ),
    );
  });

  it('runs sample observations through a draft protocol without extra setup', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'run' } as never);
    render(<ResearchExperiments {...props} data={data} />);
    fireEvent.click(screen.getByRole('button', { name: 'Run sample experiment' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        'protocol',
        'demo',
        undefined,
        'source',
        false,
      ),
    );
  });

  it('allows testing without verifying research first', () => {
    render(
      <ResearchExperiments
        {...props}
        data={{ ...data, verified: false, protocols: [{ ...protocol, current: false }] }}
      />,
    );
    expect(screen.getByRole('button', { name: 'Run sample experiment' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Go to verification' })).not.toBeInTheDocument();
  });

  it('pairs the real sample with downloads and a partial saved replay', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'run' } as never);
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['{}\n']),
    } as Response);
    const sampleState = {
      ...state,
      sources: [{ ...source, original_filename: 'Splitting-cells-DJI-027.pdf' }],
    } as unknown as Snapshot;
    render(
      <ResearchExperiments {...props} state={sampleState} data={{ ...data, protocols: [] }} />,
    );
    expect(screen.getByRole('link', { name: 'Download 30-second video' })).toHaveAttribute(
      'download',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Run sample analysis' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        undefined,
        'replay',
        expect.objectContaining({ name: 'DJI_08-first-30s.observations.jsonl' }),
        'source',
        true,
      ),
    );
  });

  it('runs the sample video with the live agent and marks it as a partial recording', async () => {
    const start = vi.spyOn(api, 'startExperiment').mockResolvedValue({ id: 'run' } as never);
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      blob: async () => new Blob(['video'], { type: 'video/mp4' }),
    } as Response);
    const sampleState = {
      ...state,
      sources: [{ ...source, original_filename: 'Splitting-cells-DJI-027.pdf' }],
    } as unknown as Snapshot;
    render(
      <ResearchExperiments {...props} state={sampleState} data={{ ...data, protocols: [] }} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Analyse sample live' }));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(
        'workspace',
        undefined,
        'video',
        expect.objectContaining({ name: 'DJI_08-first-30s.mp4', type: 'video/mp4' }),
        'source',
        true,
      ),
    );
    expect(fetch).toHaveBeenCalledWith('/demo/lsv/DJI_08-first-30s.mp4');
  });

  it('shows the reference execution, method and record views with uncertain evidence', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true, json: async () => [] } as Response);
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      })),
    );
    Element.prototype.scrollTo = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ResearchExperiments
          {...props}
          data={{
            ...data,
            runs: [
              {
                id: 'run',
                protocol_id: 'protocol',
                status: 'succeeded',
                mode: 'demo',
                filename: 'sample',
                error: null,
                created_at: '2026-10-04',
                completed_at: '2026-10-04',
                result: {
                  observations: [
                    {
                      id: 'o',
                      step_id: 's1',
                      confidence: 0.9,
                      status: 'performed',
                      values: [],
                      description: 'The setting could not be read.',
                      span: { start_s: 0, end_s: 4 },
                    },
                  ],
                  summary: { observations: 1, deviations: 1, needs_review: 1, failed_windows: 0 },
                  deviations: [
                    {
                      id: 'd',
                      kind: 'unverified_check',
                      step_id: 's1',
                      check_id: null,
                      message: 'The setting could not be read.',
                      needs_review: true,
                      expected: 50,
                      observed: null,
                      span: { start_s: 0, end_s: 4 },
                    },
                  ],
                },
              },
            ],
          }}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('tab', { name: 'Execution' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Method' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Record' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Execution timeline' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Verification coverage' })).toBeInTheDocument();
    expect(screen.getAllByText('The setting could not be read.').length).toBeGreaterThan(0);
    expect(
      screen.getByText(/synthetic observations, checked by the real protocol verifier/),
    ).toBeInTheDocument();
  });
});

it('skips a live analysis to a saved video result without starting another run', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: true, json: async () => [] } as Response);
  const start = vi.spyOn(api, 'startExperiment');
  Element.prototype.scrollTo = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const common = {
    protocol_id: protocol.id,
    mode: 'video' as const,
    filename: 'recording.mp4',
    error: null,
    created_at: '2026-10-04T10:00:00Z',
    result: {},
  };
  render(
    <QueryClientProvider client={client}>
      <ResearchExperiments
        {...props}
        data={{
          ...data,
          runs: [
            { ...common, id: 'live', status: 'running', completed_at: null },
            { ...common, id: 'saved', status: 'succeeded', completed_at: '2026-10-04T10:01:00Z' },
          ],
        }}
      />
    </QueryClientProvider>,
  );
  expect(screen.getAllByText('Checking the recording').length).toBeGreaterThan(0); // the page and the open analysis window
  fireEvent.click(screen.getByRole('button', { name: 'Skip to demo results' }));
  await waitFor(() =>
    expect(screen.getByRole('region', { name: 'Execution timeline' })).toBeInTheDocument(),
  );
  expect(screen.queryByText('Checking the recording')).not.toBeInTheDocument();
  expect(document.querySelector('.video-stage video')).toHaveAttribute(
    'src',
    '/api/experiment-runs/saved/recording',
  );
  expect(start).not.toHaveBeenCalled();
});

describe('retracted research', () => {
  const retracted = { ...source, id: 'retracted', title: 'Withdrawn trial' };
  const sound = { ...source, id: 'sound', title: 'Sound trial' };
  const withValidity = {
    ...state,
    sources: [retracted, sound],
    validity: { retracted: { status: 'invalidated' } },
  } as unknown as Snapshot;

  it('does not choose a retracted paper by default, and warns when one is chosen', () => {
    render(
      <ResearchExperiments {...props} state={withValidity} data={{ ...data, protocols: [] }} />,
    );
    const select = screen.getByLabelText('Methods from one paper');
    expect(select).toHaveValue('sound');
    expect(screen.getByRole('option', { name: '(Retracted) Withdrawn trial' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();

    fireEvent.change(select, { target: { value: 'retracted' } });
    expect(screen.getByRole('alert')).toHaveTextContent('This paper has been retracted');
  });

  it('offers sources without a methods section last, and says why they give no steps', () => {
    const abstract = {
      ...source,
      id: 'abstract',
      title: 'Abstract only',
      metadata: { fulltext_imported: false },
      excerpts: [{ id: 'a1', text: 'We found...', locator: { section: 'Abstract' }, sequence: 0 }],
    };
    const both = { ...state, sources: [abstract, source] } as unknown as Snapshot;
    render(<ResearchExperiments {...props} state={both} data={{ ...data, protocols: [] }} />);
    const select = screen.getByLabelText('Methods from one paper');
    expect(select).toHaveValue('source'); // the one with a methods section, though listed second
    const option = screen.getByRole('option', {
      name: 'Abstract only · no methods section (abstract only)',
    });
    expect(option).toBeDisabled();
    cleanup();
    const only = { ...state, sources: [abstract] } as unknown as Snapshot;
    render(<ResearchExperiments {...props} state={only} data={{ ...data, protocols: [] }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Only the abstract of this paper');
  });

  it('says why the analysis cannot run yet', () => {
    render(<ResearchExperiments {...props} data={data} />);
    expect(screen.getByText('Choose a recording to run the analysis.')).toBeInTheDocument();
    expect(screen.getByText('No recording chosen')).toBeInTheDocument();
  });
});
