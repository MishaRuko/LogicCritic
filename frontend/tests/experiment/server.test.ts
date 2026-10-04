import { describe, expect, it } from 'vitest';
import { extract, health, turn } from '../../src/lib/experiment/server';
const post = (body: unknown) => new Request('http://local.test/experiment-api/x', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
describe('Experiment API handlers (ported from the Trial Worker)', () => {
  it('reports whether Claude is configured, from either key name', async () => {
    expect(await health({}).json()).toMatchObject({ claudeConfigured: false, model: 'claude-opus-5-5' });
    expect(await health({ CLAUDE_API_KEY: 'x' }).json()).toMatchObject({ claudeConfigured: true });
    expect(await health({ ANTHROPIC_API_KEY: 'x' }).json()).toMatchObject({ claudeConfigured: true });
  });
  it('explains a missing key instead of failing silently', async () => {
    const response = await extract(post({ name: 'p.txt', document: { kind: 'text', text: '1. Add reagent.' } }), {});
    expect(response.status).toBe(503); expect((await response.json() as { error: string }).error).toContain('CLAUDE_API_KEY');
  });
  it('validates request bodies before calling the model', async () => {
    expect((await extract(post({ name: 'p.pdf', document: { kind: 'pdf', data: 'not base64!' } }), { CLAUDE_API_KEY: 'x' })).status).toBe(400);
    expect((await turn(post({ messages: [] }), { CLAUDE_API_KEY: 'x' })).status).toBe(400);
  });
});

describe('Published records (share links, file-backed)', () => {
  it('publishes hash-consistent records and serves them by short id', async () => {
    const { mkdtemp } = await import('node:fs/promises');
    const { tmpdir } = await import('node:os');
    const { join } = await import('node:path');
    const { createRecord } = await import('../../src/lib/experiment/record');
    const { cachedAnalysis, samples } = await import('../../src/lib/experiment/demo');
    const { publish, fetchPublished } = await import('../../src/lib/experiment/server');
    const env = { EXPERIMENT_RECORDS_DIR: await mkdtemp(join(tmpdir(), 'records-')) };
    const a = samples.map(cachedAnalysis).find(Boolean)!;
    const record = await createRecord(a.run, a.method);
    const created = await publish(post(record), env);
    expect(created.status).toBe(201);
    const id = record.recordHash.slice(0, 16);
    expect(await created.json()).toEqual({ id, path: `/experiment/r/${id}` });
    expect(await (await fetchPublished(id, env)).json()).toEqual(record);
    expect((await publish(post(record), env)).status).toBe(201); // republishing is idempotent
    expect((await publish(post({ ...record, method: { ...record.method, title: 'Tampered' } }), env)).status).toBe(400);
    expect((await fetchPublished('0123456789abcdef', env)).status).toBe(404);
    expect((await fetchPublished('../etc/passwd', env)).status).toBe(404);
  });
});
