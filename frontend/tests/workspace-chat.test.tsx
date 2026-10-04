import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/lib/research/api';
import { ResearchWorkspace } from '../src/components/research/ResearchWorkspace';
import type { AgentRun, Snapshot, Statement } from '../src/types/api';

vi.mock('../src/components/research/ResearchCanvas', () => ({ ResearchCanvas: ({ graph }: { graph: { nodes: { id: string; label: string }[] } }) => <div aria-label="Argument graph">{graph.nodes.map(node => <p key={node.id}>{node.label}</p>)}</div> }));
afterEach(() => { vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); });

describe('chat and graph integration', () => {
  it('uploads pasted text and PDFs as material, then switches to agent questions in the same workspace', async () => {
    window.history.replaceState(null, '', '/research');
    const workspace = { id: 'material-workspace', title: 'Research material', created_at: '2026-10-04T00:00:00Z' };
    const snapshot: Snapshot = { workspace, graph: { statements: [], reasoning_steps: [], relations: [] }, contexts: [], sources: [], jobs: [], validity: {} };
    vi.spyOn(api, 'listWorkspaces').mockResolvedValue([workspace]);
    vi.spyOn(api, 'createWorkspace').mockResolvedValue(workspace);
    vi.spyOn(api, 'fetchSnapshot').mockImplementation(async () => ({ ...snapshot }));
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', database: 'ok', redis: 'ok' });
    vi.spyOn(api, 'listAgentRuns').mockResolvedValue([]);
    vi.spyOn(api, 'listAgentEvents').mockResolvedValue([]);
    const upload = vi.spyOn(api, 'uploadResearch').mockImplementation(async (id, files) => {
      snapshot.sources = files.map((file, index) => ({ id: `source-${index}`, workspace_id: id, title: file.name, original_filename: file.name, kind: 'document', origin: 'upload', mime_type: file.type, content_hash: 'hash', external_ids: {}, metadata: {}, created_at: workspace.created_at, excerpts: [] }));
    });
    const start = vi.spyOn(api, 'startAgentRun').mockResolvedValue({ id: 'agent-run', workspace_id: workspace.id, goal_id: 'goal', question: 'What are the weaknesses in this paper?', kind: 'question', mode: 'guarded', model: 'test', status: 'queued', budgets: { max_turns: 30, max_web_searches: 10, max_total_output_tokens: 150000 }, usage: {}, error: null, final_report: null, final_statement_id: null, certainty: null, created_at: workspace.created_at, started_at: null, completed_at: null });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    try {
      render(<QueryClientProvider client={client}><ResearchWorkspace/></QueryClientProvider>);
      const text = '# Study findings\n\nAn association was reported. '.repeat(70).trim();
      const paper = new File(['PDF contents'], 'paper.pdf', { type: 'application/pdf' });
      fireEvent.change(screen.getByLabelText('Research message'), { target: { value: text } });
      fireEvent.change(screen.getByLabelText('Research documents'), { target: { files: [paper] } });
      fireEvent.keyDown(screen.getByLabelText('Research message'), { key: 'Enter' });
      await waitFor(() => expect(upload).toHaveBeenCalledOnce());
      expect(upload.mock.calls[0][0]).toBe(workspace.id);
      expect(upload.mock.calls[0][2]).toBe(true);
      const files = upload.mock.calls[0][1];
      expect(files.map(file => file.name)).toEqual(['pasted-research.md', 'paper.pdf']);
      await waitFor(() => expect(screen.getByRole('article', { name: 'Added material: paper.pdf' })).toBeVisible());
      await waitFor(() => expect(screen.getByLabelText('Research message')).toHaveValue(''));
      await waitFor(() => expect(screen.getByLabelText('Research message')).toBeEnabled());
      const reader = new FileReader();
      const pastedText = new Promise(resolve => { reader.onload = () => resolve(reader.result); });
      reader.readAsText(files[0]);
      expect(await pastedText).toBe(text);
      expect(start).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Agent mode' }));
      fireEvent.change(screen.getByLabelText('Research message'), { target: { value: 'What are the weaknesses in this paper?' } });
      fireEvent.keyDown(screen.getByLabelText('Research message'), { key: 'Enter' });
      await waitFor(() => expect(start).toHaveBeenCalledWith(workspace.id, expect.objectContaining({ question: 'What are the weaknesses in this paper?' })));
      expect(api.createWorkspace).toHaveBeenCalledOnce();
      expect(upload).toHaveBeenCalledOnce();
      await waitFor(() => expect(screen.getByLabelText('Research message')).toHaveValue(''));
      await waitFor(() => expect(screen.getByLabelText('Research message')).toBeEnabled());
    } finally { client.clear(); }
  });

  it('uses the same workspace for continuous prompts and updates its graph from the agent results', async () => {
    window.history.replaceState(null, '', '/research');
    const workspace = { id: 'conversation-workspace', title: 'Does treatment X work?', created_at: '2026-10-04T00:00:00Z' };
    const snapshot: Snapshot = { workspace, graph: { statements: [], reasoning_steps: [], relations: [] }, contexts: [], sources: [], jobs: [], validity: {} };
    let runs: AgentRun[] = [];
    vi.spyOn(api, 'listWorkspaces').mockResolvedValue([workspace]);
    vi.spyOn(api, 'createWorkspace').mockResolvedValue(workspace);
    vi.spyOn(api, 'fetchSnapshot').mockImplementation(async () => ({ ...snapshot, graph: { ...snapshot.graph } }));
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', database: 'ok', redis: 'ok' });
    vi.spyOn(api, 'listAgentRuns').mockImplementation(async () => [...runs]);
    vi.spyOn(api, 'listAgentEvents').mockResolvedValue([]);
    const upload = vi.spyOn(api, 'uploadResearch');
    const start = vi.spyOn(api, 'startAgentRun').mockImplementation(async (id, input) => {
      const statement: Statement = { id: `claim-${runs.length}`, workspace_id: id, text: `Evidence recorded for: ${input.question}`, assertion_mode: 'hypothesis', role: 'conclusion', salience: 'core', lifecycle: 'proposed', provenance: { actor_type: 'agent', actor_id: 'test' }, excerpt_ids: [], created_at: workspace.created_at };
      snapshot.graph.statements = [...snapshot.graph.statements, statement];
      const run: AgentRun = { id: `run-${runs.length}`, workspace_id: id, goal_id: 'goal', question: input.question, kind: input.kind, mode: input.mode, model: 'test', status: 'running', budgets: { max_turns: 30, max_web_searches: 10, max_total_output_tokens: 150000 }, usage: {}, error: null, final_report: null, final_statement_id: null, certainty: null, created_at: workspace.created_at, started_at: null, completed_at: null };
      runs = [run, ...runs];
      return run;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    try {
      render(<QueryClientProvider client={client}><ResearchWorkspace/></QueryClientProvider>);
      fireEvent.click(screen.getByRole('button', { name: 'Agent mode' }));
      fireEvent.change(screen.getByLabelText('Research message'), { target: { value: 'Does treatment X work?' } });
      fireEvent.keyDown(screen.getByLabelText('Research message'), { key: 'Enter' });
      await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(screen.getByLabelText('Argument graph')).toHaveTextContent('Evidence recorded for: Does treatment X work?'));
      await waitFor(() => expect(screen.getByLabelText('Research message')).toBeEnabled());
      fireEvent.change(screen.getByLabelText('Research message'), { target: { value: 'Look for conflicting human studies.' } });
      fireEvent.keyDown(screen.getByLabelText('Research message'), { key: 'Enter' });
      await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
      expect(api.createWorkspace).toHaveBeenCalledOnce();
      expect(start.mock.calls.map(call => call[0])).toEqual([workspace.id, workspace.id]);
      expect(upload).not.toHaveBeenCalled();
      await waitFor(() => expect(screen.getByLabelText('Argument graph')).toHaveTextContent('Evidence recorded for: Look for conflicting human studies.'));
      expect(screen.getByRole('log')).toHaveTextContent('Does treatment X work?');
      expect(screen.getByRole('log')).toHaveTextContent('Look for conflicting human studies.');
    } finally { client.clear(); }
  });
});
