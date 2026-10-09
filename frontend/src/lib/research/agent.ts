import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from './api';
import type { AgentCertainty, AgentEvent, AgentRun } from '../../types/api';

/** How a run's certainty reads; older runs stored 'conditional' and 'hypothesis'. */
export const CERTAINTY_LABELS: Record<AgentCertainty, string> = {
  established: 'Established',
  supported: 'Supported',
  tentative: 'Tentative',
  speculative: 'Speculative',
  conditional: 'Tentative',
  hypothesis: 'Speculative',
  abstained: 'Abstained',
};

export const isAgentActive = (run: Pick<AgentRun, 'status'>) =>
  run.status === 'queued' || run.status === 'running';
export const agentStatusLabel = (status: AgentRun['status']) =>
  ({
    queued: 'Queued',
    running: 'Researching',
    succeeded: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
    budget_exhausted: 'Budget reached',
  })[status];

export function useAgentRuns(workspace?: string) {
  return useQuery({
    queryKey: ['agent-runs', workspace],
    queryFn: () => api.listAgentRuns(workspace!),
    enabled: !!workspace,
    refetchInterval: 3000,
    retry: 1,
  });
}

/** Drain every page, including the final events of a completed historical run. */
export async function fetchAgentTrace(
  runId: string,
  previous: AgentEvent[] = [],
  signal?: AbortSignal,
) {
  const events = [...previous];
  let page: AgentEvent[];
  do {
    page = await api.listAgentEvents(runId, events.at(-1)?.seq ?? 0, signal);
    events.push(...page);
  } while (page.length === 500);
  return events;
}

export function useAgentEvents(run?: AgentRun) {
  const client = useQueryClient();
  const key = ['agent-events', run?.id];
  const trace = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchAgentTrace(run!.id, client.getQueryData<AgentEvent[]>(key), signal),
    enabled: !!run,
    refetchInterval: run && isAgentActive(run) ? 2000 : false,
    retry: 1,
  });
  const { refetch } = trace;
  // The status response can arrive just before the final trace event is written.
  useEffect(() => {
    if (!run || isAgentActive(run)) return;
    void refetch();
    const finalPoll = setTimeout(() => void refetch(), 2000);
    return () => clearTimeout(finalPoll);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch once when the run finishes, not on every status object
  }, [run?.id, run?.status, refetch]);
  return trace;
}

const text = (value: unknown) => (typeof value === 'string' ? value : '');
export function eventTitle(event: AgentEvent): string {
  const payload = event.payload;
  const names: Record<string, string> = {
    search_papers: 'Searching papers',
    follow_citations: 'Following citations',
    read_paper: 'Importing a paper',
    fetch_url: 'Reading a web page',
    read_source: 'Reading evidence',
    record_claim: 'Adding a claim',
    record_reasoning: 'Connecting the argument',
    check_conclusion: 'Checking the conclusion',
    finalize_conclusion: 'Finalizing the answer',
    abstain: 'Recording an abstention',
  };
  if (event.type === 'tool_call' || event.type === 'tool_result')
    return names[text(payload.name)] ?? text(payload.name).replaceAll('_', ' ');
  return (
    (
      {
        run_started: 'Research started',
        assistant_text: 'Research note',
        web_search: 'Searching the web',
        web_results: 'Web sources found',
        check: 'Conclusion check',
        finalization: 'Conclusion review',
        criteria_given: 'Your evidence criteria',
        criteria_proposed: 'Evidence criteria',
        criteria_default: 'Evidence criteria',
        run_finished: 'Research finished',
        nudge: 'Continuing the investigation',
        turn: 'Research step',
        thinking: 'Reasoning summary',
        server_block: 'Research activity',
      } as Record<string, string>
    )[event.type] ?? event.type.replaceAll('_', ' ')
  );
}

export function eventDescription(event: AgentEvent): string {
  const p = event.payload;
  if (event.type === 'assistant_text' || event.type === 'thinking') return text(p.text);
  if (event.type === 'web_search') return text(p.query);
  if (event.type === 'run_finished')
    return text(p.error) || agentStatusLabel(p.status as AgentRun['status']) || text(p.status);
  if (event.type === 'tool_call') {
    const input = p.input as Record<string, unknown> | undefined;
    if (!input) return '';
    if (text(p.name) === 'follow_citations')
      return `${input.direction === 'cited_by' ? 'Works citing' : 'Works cited by'} ${text(input.paper)}`;
    const purpose = text(input.purpose) === 'against' ? ' (evidence against)' : '';
    return (
      (text(input.query) && text(input.query) + purpose) ||
      text(input.url) ||
      text(input.text) ||
      text(input.explanation)
    );
  }
  if (event.type === 'tool_result' || event.type === 'check' || event.type === 'finalization') {
    const result = p.result as Record<string, unknown> | undefined;
    if (!result) return '';
    return (
      text(result.error) ||
      text(result.title) ||
      text(result.note) ||
      (typeof result.accepted === 'boolean'
        ? result.accepted
          ? 'Conclusion accepted by the verifier.'
          : 'More evidence or a narrower conclusion is needed.'
        : '')
    );
  }
  return '';
}

export function safeSourceUrl(value: unknown) {
  if (typeof value !== 'string') return undefined;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}
