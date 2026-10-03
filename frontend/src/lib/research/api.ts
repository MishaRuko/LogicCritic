import type { Context, Graph, Job, PatchOperation, Snapshot, SourceWithExcerpts, Validity, Verification, Workspace } from '../../types/api';
export const userProvenance = { actor_type: 'user', actor_id: 'workspace-reviewer' } as const;
export class ApiError extends Error {
  constructor(message: string, public status: number, public detail: unknown) { super(message); }
}
function describe(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => typeof d === 'object' && d !== null && 'msg' in d ? String(d.msg) : String(d)).join('; ');
  if (detail && typeof detail === 'object' && 'message' in detail) return String(detail.message);
  return JSON.stringify(detail) ?? 'Request failed';
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api${path}`, { ...init, cache: 'no-store' }); }
  catch { throw new Error('Cannot reach the API. Check that the backend is running, then retry.'); }
  if (response.status === 204) return undefined as T;
  const body = await response.text();
  let data: unknown;
  try { data = JSON.parse(body); } catch { data = body; }
  if (!response.ok) {
    const detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
    throw new ApiError(response.status === 502 || response.status === 503 ? `Backend unavailable: ${describe(detail)}` : describe(detail), response.status, detail);
  }
  return data as T;
}
const pendingKeys = new Map<string, string>();
async function post<T>(path: string, payload: Record<string, unknown> = {}, keyed = false): Promise<T> {
  const identity = path + JSON.stringify(payload);
  if (keyed && !pendingKeys.has(identity)) pendingKeys.set(identity, crypto.randomUUID());
  const body = JSON.stringify(keyed ? { ...payload, idempotency_key: pendingKeys.get(identity) } : payload);
  const result = await request<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
  pendingKeys.delete(identity);
  return result;
}
export const listWorkspaces = () => request<Workspace[]>('/workspaces');
export const createWorkspace = (title: string) => post<Workspace>('/workspaces', { title });
export const deleteWorkspace = (id: string) => request<void>(`/workspaces/${id}`, { method: 'DELETE' });
export const fetchGraph = (id: string) => request<Graph>(`/workspaces/${id}/graph`);
export const fetchContext = (workspace: string, statement: string) => request<Context>(`/workspaces/${workspace}/statements/${statement}/context`);
export const health = () => request<{ status: string; database: string; redis: string }>('/health/ready');
export async function uploadSource(workspace: string, file: File): Promise<SourceWithExcerpts> {
  const form = new FormData(); form.append('file', file);
  try {
    const source = await request<SourceWithExcerpts>(`/workspaces/${workspace}/sources`, { method: 'POST', body: form });
    remember(workspace, 'sourceIds', source.id); return source;
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.detail && typeof e.detail === 'object' && 'source_id' in e.detail) {
      const source = await attachSource(workspace, String(e.detail.source_id)); return source;
    }
    throw e;
  }
}
export async function attachSource(workspace: string, id: string): Promise<SourceWithExcerpts> {
  const source = await request<SourceWithExcerpts>(`/sources/${id}`);
  if (source.workspace_id !== workspace) throw new Error('This source belongs to another workspace.');
  const excerpts = await request<SourceWithExcerpts['excerpts']>(`/sources/${id}/excerpts`);
  remember(workspace, 'sourceIds', id); return { ...source, excerpts };
}
export async function extract(source: SourceWithExcerpts) {
  const job = await post<Job>(`/sources/${source.id}/extract`, {}, true);
  remember(source.workspace_id, 'jobIds', job.id); return job;
}
export async function attachJob(workspace: string, id: string) {
  const job = await request<Job>(`/extraction-jobs/${id}`);
  if (job.workspace_id !== workspace) throw new Error('This job belongs to another workspace.');
  remember(workspace, 'jobIds', job.id); return job;
}
export const cancelJob = (id: string) => request<Job>(`/extraction-jobs/${id}`, { method: 'DELETE' });
export const patchGraph = (id: string, operations: PatchOperation[]) => post<{ patch_id: string; id_map: Record<string, string>; accepted_event_ids: string[]; affected_node_ids: string[] }>(`/workspaces/${id}/graph-patches`, { operations }, true);
export const review = (id: string, node_type: 'statement' | 'reasoning_step', node_id: string, decision: 'accepted' | 'rejected') => post(`/workspaces/${id}/review`, { node_type, node_id, decision, provenance: userProvenance }, true);
export const verify = (id: string) => post<Verification>(`/workspaces/${id}/verify`);
export const checkArguments = (id: string) => post<{ event_id: string; checked_steps: number; flagged_steps: number }>(`/workspaces/${id}/argument-check`, {}, true);
export const synthesize = (id: string) => post<{ event_id: string; candidates_considered: number; proposed_links: number; audited_links: number; links_needing_review: number }>(`/workspaces/${id}/synthesize`, {}, true);
export async function setValidity(workspace: string, source: string, status: 'valid' | 'invalidated', reason: string) {
  const validity = await post<Validity>(`/sources/${source}/validity`, { status, reason, provenance: userProvenance }, true);
  const registry = readRegistry(workspace); registry.validity[source] = validity; writeRegistry(workspace, registry); return validity;
}
interface Registry { sourceIds: string[]; jobIds: string[]; validity: Record<string, Validity> }
const sessionRegistries = new Map<string, Registry>();
const storageKey = (id: string) => `logiccritic:workspace:${id}`;
export function readRegistry(id: string): Registry {
  if (sessionRegistries.has(id)) return sessionRegistries.get(id)!;
  try { const data = JSON.parse(localStorage.getItem(storageKey(id)) ?? '{}'); return { sourceIds: data.sourceIds ?? [], jobIds: data.jobIds ?? [], validity: data.validity ?? {} }; }
  catch { return { sourceIds: [], jobIds: [], validity: {} }; }
}
function writeRegistry(id: string, registry: Registry) { sessionRegistries.set(id, registry); try { localStorage.setItem(storageKey(id), JSON.stringify(registry)); } catch { /* The current session continues when browser persistence is disabled. */ } }
function remember(workspace: string, kind: 'sourceIds' | 'jobIds', id: string) {
  const registry = readRegistry(workspace); registry[kind] = [...new Set([...registry[kind], id])]; writeRegistry(workspace, registry);
}
export async function fetchSnapshot(id: string): Promise<Snapshot> {
  const [workspace, graph] = await Promise.all([request<Workspace>(`/workspaces/${id}`), fetchGraph(id)]);
  const registry = readRegistry(id);
  const [contexts, sources, jobs] = await Promise.all([
    Promise.all(graph.statements.map(s => fetchContext(id, s.id))),
    Promise.all(registry.sourceIds.map(source => attachSource(id, source))),
    Promise.all(registry.jobIds.map(job => request<Job>(`/extraction-jobs/${job}`))),
  ]);
  return { workspace, graph, contexts, sources, jobs, validity: registry.validity };
}
export function downloadSnapshot(state: Snapshot) {
  const url = URL.createObjectURL(new Blob([JSON.stringify({ format: 'logiccritic-snapshot-v1', exported_at: new Date().toISOString(), ...state }, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = `${state.workspace.title.replace(/[^a-z0-9_-]/gi, '_')}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
