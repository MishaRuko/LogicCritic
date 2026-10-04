import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import Anthropic from '@anthropic-ai/sdk';
import { z } from 'zod';
import { MODEL } from './agent';
import { agentTurn, extractMethod } from './claude';
import { validateRecord } from './record';
import type { ExperimentRecord } from './types';

/**
 * Server-side handlers for the experiment check, ported from Trial's Cloudflare Worker (lab-experiment-check e9bb2f1).
 * Next route handlers under /experiment-api call these; /api/* belongs to the FastAPI backend.
 * The key is LogicCritic's CLAUDE_API_KEY (ANTHROPIC_API_KEY also accepted) and never reaches the browser.
 */
export type Env = Record<string, string | undefined>; // process.env, or a stub in tests
const MAX_BODY = 40_000_000;
const key = (env: Env) => env.CLAUDE_API_KEY || env.ANTHROPIC_API_KEY;
const response = (body: unknown, status = 200) => Response.json(body, { status, headers: { 'Cache-Control': 'no-store' } });

const extractRequest = z.object({
  name: z.string().min(1).max(300),
  document: z.discriminatedUnion('kind', [
    z.object({ kind: z.literal('pdf'), data: z.string().min(1).regex(/^[A-Za-z0-9+/=]+$/) }),
    z.object({ kind: z.literal('text'), text: z.string().min(1).max(500_000) })
  ])
});
// Message history is produced by the shared agent loop and passed straight back to the API, which validates it.
const turnRequest = z.object({ messages: z.array(z.object({ role: z.enum(['user', 'assistant']), content: z.unknown() })).min(1).max(60) });

async function jsonBody(request: Request): Promise<unknown> {
  if (Number(request.headers.get('content-length')) > MAX_BODY) throw new Error('Request body is too large.');
  return JSON.parse(await request.text());
}

export function health(env: Env): Response {
  return response({ name: 'Experiment check', claudeConfigured: !!key(env), model: MODEL });
}

async function withClaude(env: Env, run: (client: Anthropic) => Promise<Response>): Promise<Response> {
  if (!key(env)) return response({ error: 'CLAUDE_API_KEY is not set. Add it to LogicCritic/.env and restart.' }, 503);
  try {
    return await run(new Anthropic({ apiKey: key(env) }));
  } catch (e) {
    if (e instanceof Anthropic.RateLimitError) return response({ error: 'Claude rate limit reached. Wait a moment and retry.' }, 429);
    if (e instanceof Anthropic.AuthenticationError) return response({ error: 'The Anthropic API key was rejected.' }, 502);
    if (e instanceof Anthropic.APIError) return response({ error: `Claude API error ${e.status ?? ''}: ${e.message}` }, 502);
    return response({ error: e instanceof Error ? e.message : 'Invalid request.' }, 400);
  }
}

export function extract(request: Request, env: Env): Promise<Response> {
  return withClaude(env, async client => {
    const input = extractRequest.parse(await jsonBody(request));
    return response({ method: await extractMethod(client, { name: input.name, ...input.document }) });
  });
}

export function turn(request: Request, env: Env): Promise<Response> {
  return withClaude(env, async client => {
    const input = turnRequest.parse(await jsonBody(request));
    return response(await agentTurn(client, input.messages as Anthropic.Beta.Messages.BetaMessageParam[]));
  });
}

// ---------- Published records (share links) ----------
// Trial stored these in Cloudflare KV. Here they are JSON files in a directory: EXPERIMENT_RECORDS_DIR (a Docker volume
// in compose), else frontend/.experiment-records. Fine for a single server; a database would replace it for scale.
const MAX_RECORD = 2_000_000;
/** Short, content-addressed id for a published record. */
const recordId = (record: Pick<ExperimentRecord, 'recordHash'>) => record.recordHash.slice(0, 16);
const recordsDir = (env: Env) => env.EXPERIMENT_RECORDS_DIR || join(process.cwd(), '.experiment-records');
export const recordPath = (id: string) => `/experiment/r/${id}`;

/** Stores a record only if its hashes match its contents, so a link always resolves to exactly what was generated. */
export async function publish(request: Request, env: Env): Promise<Response> {
  if (Number(request.headers.get('content-length')) > MAX_RECORD) return response({ error: 'Record is too large to publish.' }, 413);
  let record: ExperimentRecord;
  try { record = JSON.parse(await request.text()); } catch { return response({ error: 'Invalid record.' }, 400); }
  if (!record || typeof record.recordHash !== 'string' || !/^[0-9a-f]{64}$/.test(record.recordHash) || !await validateRecord(record).catch(() => false)) return response({ error: 'Record hashes do not match its contents.' }, 400);
  const dir = recordsDir(env), id = recordId(record), file = join(dir, `${id}.json`), temp = `${file}.${process.pid}.tmp`;
  await mkdir(dir, { recursive: true });
  await writeFile(temp, JSON.stringify(record));
  await rename(temp, file); // atomic replace; content-addressed, so republishing is idempotent
  return response({ id, path: recordPath(id) }, 201);
}

export async function fetchPublished(id: string, env: Env): Promise<Response> {
  if (!/^[0-9a-f]{16}$/.test(id)) return response({ error: 'Record not found.' }, 404);
  try {
    const stored = await readFile(join(recordsDir(env), `${id}.json`), 'utf8');
    return new Response(stored, { headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=31536000, immutable' } });
  } catch { return response({ error: 'Record not found.' }, 404); }
}
