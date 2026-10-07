import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/lib/research/api';
import { ResearchUncertainty } from '../src/components/research/ResearchUncertainty';
import type { AgentRun, Assurance, Context, Obligation, Snapshot } from '../src/types/api';

const clients: QueryClient[] = [];
const scale: Assurance['scale'] = [
  'unexplored',
  'exploring',
  'contested',
  'provisional',
  'well_supported',
  'settled',
];
const supported: Assurance = {
  level: 'well_supported',
  label: 'Nothing found against it',
  scale,
  holding_back: [],
};
const run: AgentRun = {
  id: 'run-1',
  workspace_id: 'workspace-1',
  goal_id: 'goal-1',
  question: 'Does this claim hold?',
  kind: 'question',
  mode: 'guarded',
  model: 'test',
  status: 'succeeded',
  budgets: { max_turns: 30, max_web_searches: 10, max_total_output_tokens: 150000 },
  usage: { assurance: supported },
  error: null,
  final_report: null,
  final_statement_id: null,
  certainty: null,
  created_at: '2026-10-04T00:00:00Z',
  started_at: null,
  completed_at: null,
};
function mount(props: Parameters<typeof ResearchUncertainty>[0] = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <ResearchUncertainty {...props} />
    </QueryClientProvider>,
  );
  return { client, bar: screen.getByRole('region', { name: 'Research uncertainty' }) };
}
beforeEach(() => {
  vi.spyOn(api, 'listAgentEvents').mockResolvedValue([]);
});
afterEach(() => {
  clients.splice(0).forEach(client => client.clear());
  vi.restoreAllMocks();
});

describe('uncertainty bar', () => {
  it('shows an unassessed state and keeps missing metrics distinct from zero', () => {
    const { bar } = mount();
    expect(within(bar).getByRole('status')).toHaveTextContent('No position yet');
    expect(within(bar).getAllByText('—')).toHaveLength(3);
    expect(api.listAgentEvents).not.toHaveBeenCalled();
  });

  it('uses run assurance and deduplicates open workspace gaps across claim contexts', () => {
    const gap = { id: 'gap-1', status: 'open' } as Obligation;
    const state: Snapshot = {
      workspace: { id: run.workspace_id, title: 'Test workspace', created_at: run.created_at },
      jobs: [],
      validity: {},
      graph: { statements: [], reasoning_steps: [], relations: [] },
      sources: [],
      contexts: [
        { obligations: [gap, { id: 'resolved', status: 'resolved' }] },
        { obligations: [gap] },
      ] as Context[],
    };
    const { bar } = mount({ run, state });
    expect(within(bar).getByRole('status')).toHaveTextContent(supported.label);
    const metrics = within(bar).getByLabelText('Workspace metrics');
    expect(metrics).toHaveTextContent('claims0sources0open gaps1');
    expect(bar).not.toHaveTextContent('%');
  });

  it('updates downwards from a live trace and exposes every reason', async () => {
    const contested: Assurance = {
      level: 'contested',
      label: 'A flaw in the argument',
      scale,
      holding_back: [
        { kind: 'direct_conflict', description: 'Two sources disagree.' },
        { kind: 'scope_leap', description: 'The conclusion goes beyond the evidence.' },
      ],
    };
    const { client, bar } = mount({ run: { ...run, status: 'running' } });
    await waitFor(() => expect(api.listAgentEvents).toHaveBeenCalled());
    client.setQueryData(
      ['agent-events', run.id],
      [{ seq: 1, type: 'assurance', payload: contested, created_at: run.created_at }],
    );
    await waitFor(() => expect(within(bar).getByRole('status')).toHaveTextContent(contested.label));
    expect(bar).toHaveTextContent('Two sources disagree.');
    expect(bar).toHaveTextContent('+1 more');
    const details = bar.querySelector('details')!;
    details.open = true;
    expect(within(bar).getByText('The conclusion goes beyond the evidence.')).toBeVisible();
  });

  it('reads assurance embedded in check events before the next run poll', async () => {
    const provisional: Assurance = {
      level: 'provisional',
      label: 'Evidence gaps remain',
      scale,
      holding_back: [],
    };
    vi.mocked(api.listAgentEvents).mockResolvedValue([
      {
        seq: 2,
        type: 'check',
        payload: { result: { assurance: provisional } },
        created_at: run.created_at,
      },
    ]);
    const { bar } = mount({ run });
    await waitFor(() =>
      expect(within(bar).getByRole('status')).toHaveTextContent(provisional.label),
    );
  });

  it('keeps baseline runs unverified even if they contain assurance or an established conclusion', () => {
    const { bar } = mount({ run: { ...run, mode: 'baseline', certainty: 'established' } });
    expect(within(bar).getByRole('status')).toHaveTextContent('Unverified');
    expect(bar).toHaveTextContent('This run has no verifier.');
    expect(bar).not.toHaveTextContent(supported.label);
  });

  it('does not imply a settled answer when agent history is unavailable', () => {
    const { bar } = mount({ run, unavailable: true });
    expect(within(bar).getByRole('status')).toHaveTextContent('Unavailable');
    expect(bar).not.toHaveTextContent(supported.label);
  });
});
