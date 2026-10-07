import { afterEach, describe, expect, it, vi } from 'vitest';
import { verifyLive } from '../src/lib/research/api';
import { verificationHighlights } from '../src/lib/research/verification';
import type { Verification, VerificationEvent } from '../src/types/api';

afterEach(() => vi.unstubAllGlobals());
const result: Verification = {
  verification_event_id: 'v',
  rules_run: ['ungrounded_statement'],
  issues_opened: 1,
  issues_resolved: 0,
  obligations_opened: 1,
  obligations_resolved: 0,
};
const events: VerificationEvent[] = [
  { type: 'started', rules: result.rules_run },
  { type: 'rule_started', rule_code: 'ungrounded_statement', node_ids: ['claim'] },
  {
    type: 'rule_completed',
    rule_code: 'ungrounded_statement',
    node_ids: ['claim'],
    findings: [{ node_id: 'claim', node_type: 'statement', message: 'No linked source · café' }],
  },
  { type: 'completed', result },
];
function stream(lines: unknown[]) {
  const bytes = new TextEncoder().encode(lines.map(line => JSON.stringify(line)).join('\n'));
  return new Response(
    new ReadableStream({
      start(controller) {
        // Split packets inside JSON and UTF-8 text, as a real network stream can do.
        for (let index = 0; index < bytes.length; index += 3)
          controller.enqueue(bytes.slice(index, index + 3));
        controller.close();
      },
    }),
    { headers: { 'Content-Type': 'application/x-ndjson' } },
  );
}
describe('live graph verification', () => {
  it('processes fragmented streamed checks in order and waits for saved completion', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(stream(events)));
    const received: VerificationEvent[] = [];
    expect(
      await verifyLive('workspace', async event => {
        received.push(event);
      }),
    ).toEqual(result);
    expect(received).toEqual(events);
  });
  it('rejects incomplete or failed streams without claiming success', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValueOnce(stream(events.slice(0, -1)))
        .mockResolvedValueOnce(stream([{ type: 'error', message: 'Verification failed' }])),
    );
    await expect(verifyLive('workspace', () => {})).rejects.toThrow('interrupted');
    await expect(verifyLive('workspace', () => {})).rejects.toThrow('Verification failed');
  });
  it('retains findings through later checks and clears active highlights on failure', () => {
    const next: VerificationEvent = {
      type: 'rule_started',
      rule_code: 'direct_conflict',
      node_ids: ['other'],
    };
    const live = {
      workspaceId: 'workspace',
      status: 'running' as const,
      events: [...events.slice(0, -1), next],
    };
    const highlights = verificationHighlights(live);
    expect([...highlights.active]).toEqual(['other']);
    expect([...highlights.attention]).toEqual(['claim']);
    expect([...highlights.checked]).toEqual(['claim']);
    expect(verificationHighlights({ ...live, status: 'failed' }).active.size).toBe(0);
  });
});
