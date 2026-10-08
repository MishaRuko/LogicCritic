import type {
  AgentEvent,
  AgentRun,
  AgentRunInput,
  Context,
  Graph,
  GraphAnswer,
  Job,
  PatchOperation,
  Snapshot,
  Source,
  SourceWithExcerpts,
  Validity,
  Verification,
  VerificationEvent,
  Workspace,
} from '../../types/api';
export const userProvenance = { actor_type: 'user', actor_id: 'workspace-reviewer' } as const;
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail: unknown,
  ) {
    super(message);
  }
}
function describe(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail))
    return detail
      .map(d => (typeof d === 'object' && d !== null && 'msg' in d ? String(d.msg) : String(d)))
      .join('; ');
  if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message);
  return JSON.stringify(detail) ?? 'Request failed';
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...init, cache: 'no-store' });
  } catch (error) {
    if (init?.signal?.aborted) throw error;
    throw new Error('Cannot reach the API. Check that the backend is running, then retry.');
  }
  if (response.status === 204) return undefined as T;
  const body = await response.text();
  let data: unknown;
  try {
    data = JSON.parse(body);
  } catch {
    data = body;
  }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
    throw new ApiError(
      response.status === 502 || response.status === 503
        ? `Backend unavailable: ${describe(detail)}`
        : describe(detail),
      response.status,
      detail,
    );
  }
  return data as T;
}
const pendingKeys = new Map<string, string>();
async function post<T>(
  path: string,
  payload: Record<string, unknown> = {},
  keyed = false,
): Promise<T> {
  const identity = path + JSON.stringify(payload);
  if (keyed && !pendingKeys.has(identity)) pendingKeys.set(identity, crypto.randomUUID());
  const body = JSON.stringify(
    keyed ? { ...payload, idempotency_key: pendingKeys.get(identity) } : payload,
  );
  const result = await request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body,
  });
  pendingKeys.delete(identity);
  return result;
}
export const listWorkspaces = () => request<Workspace[]>('/workspaces');
export const createWorkspace = (title: string) => post<Workspace>('/workspaces', { title });
export const deleteWorkspace = (id: string) =>
  request<void>(`/workspaces/${id}`, { method: 'DELETE' });
export const fetchGraph = (id: string) => request<Graph>(`/workspaces/${id}/graph`);
export const fetchContext = (workspace: string, statement: string) =>
  request<Context>(`/workspaces/${workspace}/statements/${statement}/context`);
export const health = () =>
  request<{ status: string; database: string; redis: string }>('/health/ready');
export const listAgentRuns = (workspace: string) =>
  request<AgentRun[]>(`/workspaces/${workspace}/agent-runs`);
export const startAgentRun = (workspace: string, input: AgentRunInput) =>
  post<AgentRun>(`/workspaces/${workspace}/agent-runs`, { ...input }, true);
export const askGraph = (
  workspace: string,
  question: string,
  history: { question: string; answer: string }[] = [],
) => post<GraphAnswer>(`/workspaces/${workspace}/graph-questions`, { question, history });
export const cancelAgentRun = (id: string) =>
  request<AgentRun>(`/agent-runs/${id}`, { method: 'DELETE' });
export const listAgentEvents = (id: string, after = 0, signal?: AbortSignal) =>
  request<AgentEvent[]>(`/agent-runs/${id}/events?after=${after}&limit=500`, { signal });
export const listSources = (workspace: string) =>
  request<Source[]>(`/workspaces/${workspace}/sources`);
export async function fetchValidity(source: string): Promise<Validity | undefined> {
  try {
    return await request<Validity>(`/sources/${source}/validity`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return undefined;
    throw error;
  }
}
export interface UploadProgress {
  stage: 'uploading' | 'processing' | 'queued' | 'saved' | 'failed';
  percent: number;
  filename: string;
  current: number;
  total: number;
  jobIds?: string[];
}
function uploadWithProgress(
  path: string,
  form: FormData,
  onProgress: (percent: number) => void,
): Promise<SourceWithExcerpts> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api${path}`);
    xhr.timeout = 240_000;
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    };
    xhr.upload.onload = () => onProgress(100);
    xhr.onerror = () =>
      reject(new Error('The upload was interrupted. Check your connection and try again.'));
    xhr.onabort = () => reject(new Error('Upload cancelled.'));
    xhr.ontimeout = () =>
      reject(new Error('The upload took too long. Try a smaller document or retry.'));
    xhr.onload = () => {
      let data: unknown;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = xhr.responseText;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as SourceWithExcerpts);
      else {
        const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
        reject(new ApiError(describe(detail), xhr.status, detail));
      }
    };
    xhr.send(form);
  });
}
export async function uploadSource(
  workspace: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<SourceWithExcerpts> {
  const form = new FormData();
  form.append('file', file);
  try {
    const path = `/workspaces/${workspace}/sources`;
    const source = onProgress
      ? await uploadWithProgress(path, form, onProgress)
      : await request<SourceWithExcerpts>(path, { method: 'POST', body: form });
    remember(workspace, 'sourceIds', source.id);
    return source;
  } catch (e) {
    if (
      e instanceof ApiError &&
      e.status === 409 &&
      e.detail &&
      typeof e.detail === 'object' &&
      'source_id' in e.detail
    ) {
      const source = await attachSource(workspace, String(e.detail.source_id));
      return source;
    }
    throw e;
  }
}
export async function uploadResearch(
  workspace: string,
  files: File[],
  automatic: boolean,
  onProgress: (progress: UploadProgress) => void,
) {
  const sources: SourceWithExcerpts[] = [];
  const jobIds: string[] = [];
  let progress: UploadProgress = {
    stage: 'uploading',
    percent: 0,
    filename: files[0].name,
    current: 1,
    total: files.length,
  };
  try {
    for (const [index, file] of files.entries()) {
      progress = {
        stage: 'uploading',
        percent: Math.round((index / files.length) * 100),
        filename: file.name,
        current: index + 1,
        total: files.length,
      };
      onProgress(progress);
      sources.push(
        await uploadSource(workspace, file, percent => {
          progress = {
            ...progress,
            stage: percent === 100 ? 'processing' : 'uploading',
            percent: Math.round(((index + percent / 100) / files.length) * 100),
          };
          onProgress(progress);
        }),
      );
    }
    if (automatic) {
      for (const source of sources) {
        progress = {
          ...progress,
          stage: 'processing',
          percent: 100,
          filename: source.original_filename,
        };
        onProgress(progress);
        const job = await extract(source);
        jobIds.push(job.id);
      }
    }
    onProgress({ ...progress, stage: automatic ? 'queued' : 'saved', percent: 100, jobIds });
  } catch (error) {
    onProgress({ ...progress, stage: 'failed' });
    throw error;
  }
}
export async function attachSource(workspace: string, id: string): Promise<SourceWithExcerpts> {
  const source = await request<SourceWithExcerpts>(`/sources/${id}`);
  if (source.workspace_id !== workspace)
    throw new Error('This source belongs to another workspace.');
  const excerpts = await request<SourceWithExcerpts['excerpts']>(`/sources/${id}/excerpts`);
  remember(workspace, 'sourceIds', id);
  return { ...source, excerpts };
}
export async function extract(source: SourceWithExcerpts) {
  const job = await post<Job>(`/sources/${source.id}/extract`, {}, true);
  remember(source.workspace_id, 'jobIds', job.id);
  return job;
}
export async function attachJob(workspace: string, id: string) {
  const job = await request<Job>(`/extraction-jobs/${id}`);
  if (job.workspace_id !== workspace) throw new Error('This job belongs to another workspace.');
  remember(workspace, 'jobIds', job.id);
  return job;
}
export const cancelJob = (id: string) =>
  request<Job>(`/extraction-jobs/${id}`, { method: 'DELETE' });
export const patchGraph = (id: string, operations: PatchOperation[]) =>
  post<{
    patch_id: string;
    id_map: Record<string, string>;
    accepted_event_ids: string[];
    affected_node_ids: string[];
  }>(`/workspaces/${id}/graph-patches`, { operations }, true);
export const review = (
  id: string,
  node_type: 'statement' | 'reasoning_step',
  node_id: string,
  decision: 'accepted' | 'rejected',
) =>
  post(
    `/workspaces/${id}/review`,
    { node_type, node_id, decision, provenance: userProvenance },
    true,
  );
export const verify = (id: string) => post<Verification>(`/workspaces/${id}/verify`);
export async function verifyLive(
  id: string,
  onEvent: (event: VerificationEvent) => void | Promise<void>,
  signal?: AbortSignal,
): Promise<Verification> {
  const response = await fetch(`/api/workspaces/${id}/verify/stream`, {
    method: 'POST',
    cache: 'no-store',
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      describe(body.detail ?? 'Verification could not start.'),
      response.status,
      body,
    );
  }
  if (!response.body) throw new Error('Live verification is unavailable. Please retry.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let result: Verification | undefined;
  async function consume(line: string) {
    if (!line.trim()) return;
    const event = JSON.parse(line) as VerificationEvent;
    if (event.type === 'error') throw new Error(event.message);
    await onEvent(event);
    if (event.type === 'completed') result = event.result;
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        signal?.throwIfAborted();
        await consume(line);
      }
      if (done) break;
    }
    await consume(buffer);
  } finally {
    reader.releaseLock();
  }
  if (!result)
    throw new Error('Verification was interrupted before results were saved. Please retry.');
  return result;
}
export const fetchExperiments = (id: string) =>
  request<import('../../types/api').Experiments>(`/workspaces/${id}/experiments`);
export function startExperiment(
  id: string,
  protocol: string | undefined,
  mode: 'demo' | 'replay' | 'video' | 'live',
  file?: File,
  source?: string,
  partial = false,
) {
  const body = new FormData();
  if (protocol) body.append('protocol_id', protocol);
  if (source) body.append('source_id', source);
  body.append('mode', mode);
  body.append('partial_recording', String(partial));
  if (file) body.append('file', file);
  return request<import('../../types/api').ExperimentRun>(`/workspaces/${id}/experiment-runs`, {
    method: 'POST',
    body,
  });
}
export const fetchExperimentRun = (run: string) =>
  request<import('../../types/api').ExperimentRun>(`/experiment-runs/${run}`);
/** Still frames from a live camera, each with its time in seconds since the stream began. */
export function sendLiveFrames(run: string, frames: { blob: Blob; seconds: number }[]) {
  const body = new FormData();
  for (const frame of frames) {
    body.append('frames', frame.blob, `${Math.round(frame.seconds * 1000)}.jpg`);
    body.append('timestamps', String(frame.seconds));
  }
  return request<{ received: number; max_seconds: number }>(`/experiment-runs/${run}/frames`, {
    method: 'POST',
    body,
  });
}
/** One piece of the camera's recording; pieces are appended in order on the server. */
export const sendRecordingPiece = (run: string, index: number, piece: Blob) =>
  request<{ stored: number }>(`/experiment-runs/${run}/recording?index=${index}`, {
    method: 'POST',
    headers: { 'Content-Type': piece.type || 'video/webm' },
    body: piece,
  });
export const stopLiveSession = (run: string) =>
  request<import('../../types/api').ExperimentRun>(`/experiment-runs/${run}/stop`, {
    method: 'POST',
  });
export const checkArguments = (id: string) =>
  post<{ event_id: string; checked_steps: number; flagged_steps: number }>(
    `/workspaces/${id}/argument-check`,
    {},
    true,
  );
export const synthesize = (id: string) =>
  post<{
    event_id: string;
    candidates_considered: number;
    proposed_links: number;
    audited_links: number;
    links_needing_review: number;
  }>(`/workspaces/${id}/synthesize`, {}, true);
export async function setValidity(
  workspace: string,
  source: string,
  status: 'valid' | 'invalidated',
  reason: string,
) {
  const validity = await post<Validity>(
    `/sources/${source}/validity`,
    { status, reason, provenance: userProvenance },
    true,
  );
  const registry = readRegistry(workspace);
  registry.validity[source] = validity;
  writeRegistry(workspace, registry);
  return validity;
}
interface Registry {
  sourceIds: string[];
  jobIds: string[];
  validity: Record<string, Validity>;
}
const sessionRegistries = new Map<string, Registry>();
const storageKey = (id: string) => `logiccritic:workspace:${id}`;
export function readRegistry(id: string): Registry {
  if (sessionRegistries.has(id)) return sessionRegistries.get(id)!;
  try {
    const data = JSON.parse(localStorage.getItem(storageKey(id)) ?? '{}');
    return {
      sourceIds: data.sourceIds ?? [],
      jobIds: data.jobIds ?? [],
      validity: data.validity ?? {},
    };
  } catch {
    return { sourceIds: [], jobIds: [], validity: {} };
  }
}
function writeRegistry(id: string, registry: Registry) {
  sessionRegistries.set(id, registry);
  try {
    localStorage.setItem(storageKey(id), JSON.stringify(registry));
  } catch {
    /* The current session continues when browser persistence is disabled. */
  }
}
function remember(workspace: string, kind: 'sourceIds' | 'jobIds', id: string) {
  const registry = readRegistry(workspace);
  registry[kind] = [...new Set([...registry[kind], id])];
  writeRegistry(workspace, registry);
}
export async function fetchSnapshot(id: string): Promise<Snapshot> {
  const [workspace, graph, sourceList] = await Promise.all([
    request<Workspace>(`/workspaces/${id}`),
    fetchGraph(id),
    listSources(id),
  ]);
  const registry = readRegistry(id);
  const [contexts, sources, jobs, decisions] = await Promise.all([
    Promise.all(graph.statements.map(s => fetchContext(id, s.id))),
    Promise.all(sourceList.map(source => attachSource(id, source.id))),
    Promise.all(registry.jobIds.map(job => request<Job>(`/extraction-jobs/${job}`))),
    Promise.all(sourceList.map(source => fetchValidity(source.id))),
  ]);
  const validity = Object.fromEntries(
    decisions
      .filter((decision): decision is Validity => !!decision)
      .map(decision => [decision.source_id, decision]),
  );
  return { workspace, graph, contexts, sources, jobs, validity };
}
export function downloadSnapshot(state: Snapshot) {
  const url = URL.createObjectURL(
    new Blob(
      [
        JSON.stringify(
          { format: 'logiccritic-snapshot-v1', exported_at: new Date().toISOString(), ...state },
          null,
          2,
        ),
      ],
      { type: 'application/json' },
    ),
  );
  const a = document.createElement('a');
  a.href = url;
  a.download = `${state.workspace.title.replace(/[^a-z0-9_-]/gi, '_')}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
