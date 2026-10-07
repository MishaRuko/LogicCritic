import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/lib/research/api';
import { fetchAgentTrace, safeSourceUrl } from '../src/lib/research/agent';
import { ResearchChat } from '../src/components/research/ResearchChat';
import type { ResearchComposerMode } from '../src/components/research/ResearchUpload';
import type { AgentEvent, AgentRun } from '../src/types/api';

const clients: QueryClient[] = [];
const run: AgentRun = {
  id: 'run-1',
  workspace_id: 'workspace-1',
  goal_id: 'goal-1',
  question: 'Does the evidence support this claim?',
  kind: 'question',
  mode: 'guarded',
  model: 'backend-selected-model',
  status: 'queued',
  budgets: { max_turns: 30, max_web_searches: 10, max_total_output_tokens: 150000 },
  usage: {},
  error: null,
  final_report: null,
  final_statement_id: null,
  certainty: null,
  created_at: '2026-10-04T00:00:00Z',
  started_at: null,
  completed_at: null,
};
function mount(runs: AgentRun[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const onSelect = vi.fn();
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  const onStop = vi.fn();
  const onExperiment = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ResearchChat
        mode="agent"
        onModeChange={() => {}}
        workspaceId="workspace-1"
        runs={runs}
        loading={false}
        error={null}
        busy={false}
        layout={{ dock: 'centre', open: true, details: true }}
        onLayout={() => {}}
        onSubmit={onSubmit}
        onStop={onStop}
        onExperiment={onExperiment}
        onSelect={onSelect}
        onRetry={() => {}}
        onCancelExtraction={() => {}}
      />
    </QueryClientProvider>,
  );
  return { client, onSelect, onSubmit, onStop, onExperiment };
}
beforeEach(() => {
  vi.spyOn(api, 'listAgentEvents').mockResolvedValue([]);
});
afterEach(() => {
  clients.splice(0).forEach(client => client.clear());
  vi.restoreAllMocks();
});

describe('research conversation', () => {
  it('offers to run a completed agent protocol from the summary above chat', () => {
    const completed = {
      ...run,
      status: 'succeeded' as const,
      protocol: {
        source_id: 'protocol-source',
        title: 'Protocol',
        basis: 'Research',
        steps: [],
        experiment_protocol_id: 'prepared',
      },
    };
    const { onExperiment } = mount([completed]);
    const summary = screen.getByRole('status', { name: 'Agent summary' });
    expect(summary).toHaveTextContent('Run the experiment now?');
    fireEvent.click(within(summary).getByRole('button', { name: 'Run experiment now' }));
    expect(onExperiment).toHaveBeenCalledWith(completed);
  });

  it('does not offer an experiment for a run without a protocol or still running', () => {
    mount([
      {
        ...run,
        status: 'running',
        protocol: { source_id: 'source', title: 'Protocol', basis: '', steps: [] },
      },
    ]);
    expect(screen.queryByRole('button', { name: 'Run experiment now' })).not.toBeInTheDocument();
  });

  it('allows material uploads when agent history is unavailable and lets the user switch modes', async () => {
    const client = new QueryClient();
    clients.push(client);
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const error = new Error('Agent history unavailable');
    function Chat() {
      const [mode, setMode] = useState<ResearchComposerMode>('agent');
      return (
        <ResearchChat
          mode={mode}
          onModeChange={setMode}
          workspaceId="workspace-1"
          runs={[]}
          loading={false}
          error={error}
          busy={false}
          layout={{ dock: 'centre', open: true, details: true }}
          onLayout={() => {}}
          onSubmit={onSubmit}
          onStop={() => {}}
          onSelect={() => {}}
          onRetry={() => {}}
          onCancelExtraction={() => {}}
        />
      );
    }
    render(
      <QueryClientProvider client={client}>
        <Chat />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByLabelText('Research message'), {
      target: { value: 'Pasted research findings.' },
    });
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Material mode' }));
    expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ mode: 'material', prompt: 'Pasted research findings.' }),
      ),
    );
  });

  it('sends a prompt from the chat box with evidence options only on explicit submission', async () => {
    const { onSubmit } = mount();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Research message'), {
      target: { value: '  Does treatment X work?  ' },
    });
    fireEvent.change(screen.getByLabelText('Research goal'), { target: { value: 'hypothesis' } });
    fireEvent.change(screen.getByLabelText('Completion criteria'), {
      target: { value: ' Human trials \n\n Replication ' },
    });
    fireEvent.change(screen.getByLabelText('Maximum web searches'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        mode: 'agent',
        prompt: 'Does treatment X work?',
        files: [],
        options: {
          kind: 'hypothesis',
          mode: 'guarded',
          completion_criteria: ['Human trials', 'Replication'],
          falsifiers: [],
          max_turns: 20,
          max_web_searches: 0,
        },
      }),
    );
  });

  it('lets the user queue follow-ups while another message is researching', async () => {
    const { onSubmit } = mount([{ ...run, status: 'running' }]);
    fireEvent.change(screen.getByLabelText('Research message'), {
      target: { value: 'Now look for evidence against that conclusion.' },
    });
    expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled();
    fireEvent.keyDown(screen.getByLabelText('Research message'), { key: 'Enter' });
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ prompt: 'Now look for evidence against that conclusion.' }),
      ),
    );
    expect(screen.getByText(/Keep prompting/)).toBeVisible();
  });

  it('keeps user prompts and replies in chronological order across reloads', async () => {
    vi.mocked(api.listAgentEvents).mockResolvedValue([]);
    mount([
      {
        ...run,
        id: 'run-2',
        question: 'What about human trials?',
        status: 'succeeded',
        final_report: 'Human trials remain necessary.',
      },
      { ...run, status: 'succeeded', final_report: 'There is preclinical evidence.' },
    ]);
    const exchanges = within(
      screen.getByRole('log', { name: 'Research conversation' }),
    ).getAllByRole('article');
    expect(exchanges[0]).toHaveTextContent(run.question);
    expect(exchanges[0]).toHaveTextContent('There is preclinical evidence.');
    expect(exchanges[1]).toHaveTextContent('What about human trials?');
    expect(exchanges[1]).toHaveTextContent('Human trials remain necessary.');
  });

  it('shows live research notes and lets the user stop the running message', async () => {
    vi.mocked(api.listAgentEvents).mockResolvedValue([
      {
        seq: 1,
        type: 'assistant_text',
        payload: { text: 'Reading the clinical studies now.' },
        created_at: run.created_at,
      },
    ]);
    const { onStop } = mount([{ ...run, status: 'running' }]);
    expect(await screen.findByText('Reading the clinical studies now.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(onStop).toHaveBeenCalledWith(expect.objectContaining({ id: run.id }));
  });

  it('shows the final certainty and links back to the graph conclusion', () => {
    const { onSelect } = mount([
      {
        ...run,
        status: 'succeeded',
        final_report: 'A narrower conclusion is supported.',
        certainty: 'conditional',
        final_statement_id: 'conclusion-1',
      },
    ]);
    expect(screen.getByText('A narrower conclusion is supported.')).toBeVisible();
    expect(screen.getByText('conditional')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect conclusion' }));
    expect(onSelect).toHaveBeenCalledWith('conclusion-1');
  });

  it('shows queued follow-ups with a cancellation control', () => {
    const queued = { ...run, id: 'follow-up', question: 'Look for a replication.' };
    const { onStop } = mount([queued, { ...run, status: 'running' }]);
    expect(screen.getByText(/1 follow-up queued/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onStop).toHaveBeenCalledWith(queued);
  });
});

describe('agent trace contract', () => {
  it('drains full pages and resumes after the last recorded sequence without losing events', async () => {
    const event = (seq: number): AgentEvent => ({
      seq,
      type: 'assistant_text',
      payload: {},
      created_at: run.created_at,
    });
    vi.mocked(api.listAgentEvents)
      .mockResolvedValueOnce(Array.from({ length: 500 }, (_, index) => event(index + 2)))
      .mockResolvedValueOnce([event(502)]);
    const events = await fetchAgentTrace('run-1', [event(1)]);
    expect(api.listAgentEvents).toHaveBeenNthCalledWith(1, 'run-1', 1, undefined);
    expect(api.listAgentEvents).toHaveBeenNthCalledWith(2, 'run-1', 501, undefined);
    expect(events).toHaveLength(502);
    expect(events.at(-1)?.seq).toBe(502);
  });

  it('only links web sources with http or https URLs', () => {
    expect(safeSourceUrl('https://example.com/paper')).toBe('https://example.com/paper');
    expect(safeSourceUrl('javascript:alert(1)')).toBeUndefined();
    expect(safeSourceUrl('file:///private/paper')).toBeUndefined();
  });
});
