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
