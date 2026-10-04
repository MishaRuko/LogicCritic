import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/lib/research/api';
import type { SourceWithExcerpts } from '../src/types/api';
afterEach(() => { vi.unstubAllGlobals(); });
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }); }
describe('backend contract', () => {
  it('reuses the agent idempotency key after a failed request and preserves a zero search budget', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ detail: 'temporary failure' }, 502)).mockResolvedValueOnce(response({ id: 'run' }, 202));
    vi.stubGlobal('fetch', fetch);
    const input = { question: 'Can this claim be supported?', kind: 'claim' as const, mode: 'guarded' as const, completion_criteria: [], falsifiers: [], max_web_searches: 0 };
    await expect(api.startAgentRun('agent-workspace', input)).rejects.toThrow('temporary failure');
    await api.startAgentRun('agent-workspace', input);
    expect(fetch.mock.calls[0][0]).toBe('/api/workspaces/agent-workspace/agent-runs');
    const first = JSON.parse(fetch.mock.calls[0][1].body);
    expect(first).toMatchObject({ ...input, idempotency_key: expect.any(String) });
    expect(JSON.parse(fetch.mock.calls[1][1].body).idempotency_key).toBe(first.idempotency_key);
  });

  it('loads agent-imported sources and exact excerpts even with an empty browser registry', async () => {
    const workspace = { id: 'agent-source-workspace', title: 'Research' };
    const source = { id: 'agent-source', workspace_id: workspace.id, original_filename: 'web-page.txt' };
    const excerpts = [{ id: 'agent-excerpt', source_id: source.id, text: 'Exact evidence imported by the agent.' }];
    const validity = { id: 'validity', source_id: source.id, status: 'invalidated', reason: 'Retracted paper', provenance: { actor_type: 'integration' } };
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (path: string) => {
      const responses: Record<string, unknown> = {
        [`/api/workspaces/${workspace.id}`]: workspace,
        [`/api/workspaces/${workspace.id}/graph`]: { statements: [], reasoning_steps: [], relations: [] },
        [`/api/workspaces/${workspace.id}/sources`]: [source],
        [`/api/sources/${source.id}`]: source,
        [`/api/sources/${source.id}/excerpts`]: excerpts,
        [`/api/sources/${source.id}/validity`]: validity,
      };
      return response(responses[path]);
    }));
    expect(api.readRegistry(workspace.id).sourceIds).toEqual([]);
    const snapshot = await api.fetchSnapshot(workspace.id);
    expect(snapshot.sources).toEqual([{ ...source, excerpts }]);
    expect(snapshot.validity[source.id]).toEqual(validity);
  });
  it('treats a missing source-validity decision as unknown and surfaces other backend failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({ detail: 'No validity decision recorded' }, 404)).mockResolvedValueOnce(response({ detail: 'Database unavailable' }, 503)));
    expect(await api.fetchValidity('new-source')).toBeUndefined();
    await expect(api.fetchValidity('new-source')).rejects.toThrow('Database unavailable');
  });
  it('posts user-only review provenance and preserves an idempotency key across failed retries', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response({ detail: 'temporary failure' }, 502)).mockResolvedValueOnce(response({ lifecycle: 'accepted' })).mockResolvedValueOnce(response({ lifecycle: 'rejected' }));
    vi.stubGlobal('fetch', fetch);
    await expect(api.review('workspace', 'statement', 'node', 'accepted')).rejects.toThrow('temporary failure');
    await api.review('workspace', 'statement', 'node', 'accepted');
    const first = JSON.parse(fetch.mock.calls[0][1].body); const second = JSON.parse(fetch.mock.calls[1][1].body);
    expect(fetch.mock.calls[0][0]).toBe('/api/workspaces/workspace/review');
    expect(first).toMatchObject({ node_type: 'statement', node_id: 'node', decision: 'accepted', provenance: { actor_type: 'user' } });
    expect(first.idempotency_key).toBe(second.idempotency_key);
    await api.review('workspace', 'statement', 'node', 'rejected');
    expect(JSON.parse(fetch.mock.calls[2][1].body).idempotency_key).not.toBe(first.idempotency_key);
  });
  it('surfaces validation details and missing credentials without inventing successful output', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({ detail: [{ msg: 'Invalid premise' }] }, 422)).mockResolvedValueOnce(response({ detail: 'Claude argument checking is unavailable' }, 503)));
    await expect(api.patchGraph('workspace', [])).rejects.toThrow('Invalid premise');
    await expect(api.checkArguments('workspace')).rejects.toThrow('Claude argument checking is unavailable');
  });
  it('reconnects duplicate uploads to the existing backend source and loads exact excerpts', async () => {
    const source = { id: 'source-duplicate', workspace_id: 'workspace-duplicate', original_filename: 'notes.md' };
    const excerpts = [{ id: 'excerpt', source_id: source.id, text: 'Exact quote.', locator: { start: 0, end: 12 } }];
    const fetch = vi.fn().mockResolvedValueOnce(response({ detail: { message: 'Already exists', source_id: source.id } }, 409)).mockResolvedValueOnce(response(source)).mockResolvedValueOnce(response(excerpts));
    vi.stubGlobal('fetch', fetch);
    const result = await api.uploadSource(source.workspace_id, new File(['Exact quote.'], 'notes.md', { type: 'text/markdown' }));
    expect(result.excerpts).toEqual(excerpts);
    expect(fetch.mock.calls[0][1].body).toBeInstanceOf(FormData);
    expect(fetch.mock.calls[0][1].headers).toBeUndefined();
    expect(api.readRegistry(source.workspace_id).sourceIds).toContain(source.id);
  });
  it('never attaches sources or jobs from another workspace', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(response({ id: 'other-source', workspace_id: 'other-workspace' }))));
    await expect(api.attachSource('workspace-isolated', 'other-source')).rejects.toThrow('another workspace');
    expect(api.readRegistry('workspace-isolated').sourceIds).toEqual([]);
    await expect(api.attachJob('workspace-isolated', 'other-job')).rejects.toThrow('another workspace');
  });
  it('remembers extraction IDs and sends the backend extraction contract', async () => {
    const source = { id: 'source-extract', workspace_id: 'workspace-extract' } as SourceWithExcerpts;
    const fetch = vi.fn().mockResolvedValue(response({ id: 'job-extract', source_id: source.id, workspace_id: source.workspace_id, status: 'queued' }));
    vi.stubGlobal('fetch', fetch);
    await api.extract(source);
    expect(fetch.mock.calls[0][0]).toBe('/api/sources/source-extract/extract');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ idempotency_key: expect.any(String) });
    expect(api.readRegistry(source.workspace_id).jobIds).toContain('job-extract');
  });
  it('reports transfer progress, then waits for the PDF to be processed before marking it saved', async () => {
    const source = { id: 'pdf-progress', workspace_id: 'workspace-progress', original_filename: 'paper.pdf', excerpts: [] };
    const xhr = {
      status: 201, responseText: JSON.stringify(source), open: vi.fn(),
      upload: { onprogress: null as ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null, onload: null as (() => void) | null },
      onload: null as (() => void) | null,
      send: vi.fn(),
    };
    vi.stubGlobal('XMLHttpRequest', vi.fn(function () { return xhr; }));
    const onProgress = vi.fn();
    const pending = api.uploadResearch(source.workspace_id, [new File(['PDF'], 'paper.pdf', { type: 'application/pdf' })], false, onProgress);
    expect(xhr.open).toHaveBeenCalledWith('POST', `/api/workspaces/${source.workspace_id}/sources`);
    expect(xhr.send.mock.calls[0][0]).toBeInstanceOf(FormData);
    xhr.upload.onprogress!({ lengthComputable: true, loaded: 50, total: 100 });
    expect(onProgress).toHaveBeenLastCalledWith(expect.objectContaining({ stage: 'uploading', percent: 50 }));
    xhr.upload.onload!();
    expect(onProgress).toHaveBeenLastCalledWith(expect.objectContaining({ stage: 'processing', percent: 100 }));
    xhr.onload!();
    await pending;
    expect(onProgress).toHaveBeenLastCalledWith(expect.objectContaining({ stage: 'saved', percent: 100 }));
    expect(api.readRegistry(source.workspace_id).sourceIds).toContain(source.id);
  });
  it('surfaces PDF parsing errors and reports failed progress instead of a completed upload', async () => {
    const xhr = { status: 422, responseText: JSON.stringify({ detail: 'This PDF needs OCR.' }), upload: {}, open: vi.fn(), onload: null as (() => void) | null, send: vi.fn() };
    vi.stubGlobal('XMLHttpRequest', vi.fn(function () { return xhr; }));
    const onProgress = vi.fn();
    const pending = api.uploadResearch('workspace-bad-pdf', [new File(['PDF'], 'scan.pdf')], true, onProgress);
    const rejected = expect(pending).rejects.toThrow('This PDF needs OCR.');
    xhr.onload!();
    await rejected;
    expect(onProgress).toHaveBeenLastCalledWith(expect.objectContaining({ stage: 'failed' }));
    expect(api.readRegistry('workspace-bad-pdf').sourceIds).toEqual([]);
  });
});
