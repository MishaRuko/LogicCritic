import { afterEach, describe, expect, it, vi } from 'vitest';
import * as api from '../src/lib/research/api';
import type { SourceWithExcerpts } from '../src/types/api';
afterEach(() => { vi.unstubAllGlobals(); });
function response(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }); }
describe('backend contract', () => {
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
});
