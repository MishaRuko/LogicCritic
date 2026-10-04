import { evidenceDebt, verifyRun } from './conformance';
import { sha256 } from './hashing';
import type { ExperimentRecord, MethodContract, RecordSnapshot, Run } from './types';
export async function createRecord(run: Run, method: MethodContract, snapshot?: RecordSnapshot): Promise<ExperimentRecord> {
  const results = verifyRun(method, run);
  const agent = run.observations.some(o => o.provenance === 'claude_agent');
  const body = { schemaVersion: 3 as const, experimentId: run.id, generatedAt: new Date().toISOString(), method, methodHash: await sha256(method), run, results, evidenceDebt: evidenceDebt(method, results), observationHash: await sha256(run.observations), limitations: [
    agent ? `Observations were produced by a Claude agent inspecting sampled frames. Sampled frames cannot establish continuous durations.` : 'Cached observations combine model-assisted frame review with published dataset annotations. This is not an independent model benchmark.',
    'Verification states are decided by deterministic rules over the reported observations. A step is verified when its core visual checks are confirmed with timestamped evidence (detail checks it could not show are listed with it); a contradiction needs confidence of at least 0.8. A step is reported skipped only when the agent watched where it belongs and saw it not done, and it is not a long wait; look-alike steps are reported as one of their group.',
    'Hashes establish integrity of the method and record; they do not prove that a physical event occurred.',
    'Confidence is an estimate, not a calibrated probability.'
  ], ...(snapshot && { snapshot }) };
  return { ...body, recordHash: await sha256(body) };
}
export async function validateRecord(record: ExperimentRecord): Promise<boolean> {
  const { recordHash, ...body } = record;
  return recordHash === await sha256(body) && record.methodHash === await sha256(record.method) && record.observationHash === await sha256(record.run.observations);
}
/** Publishes a record to the Worker and returns its shareable URL. Content-addressed, so republishing is idempotent. */
export async function publishRecord(record: ExperimentRecord): Promise<string> {
  const response = await fetch('/api/records', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(record) });
  const body = await response.json().catch(() => ({})) as { path?: string; error?: string };
  if (!response.ok || !body.path) throw new Error(body.error ?? 'Could not publish the record.');
  return new URL(body.path, location.origin).href;
}
export async function fetchRecord(id: string): Promise<ExperimentRecord> {
  const response = await fetch(`/api/records/${encodeURIComponent(id)}`);
  if (response.status === 404) throw new Error('This record link does not exist.');
  if (!response.ok) throw new Error('The record could not be loaded.');
  return response.json();
}
/** The moment worth showing: the first contradiction's evidence, else the first verified step, else mid-recording. */
export function keyMoment(run: Run, method: MethodContract): { t: number; stepId?: string; caption: string } {
  const results = verifyRun(method, run);
  const pick = (status: string) => method.requirements.map(r => run.observations.find(o => o.stepId === r.id && results[r.id]?.status === status)).find(Boolean);
  const o = pick('contradicted') ?? pick('verified') ?? [...run.observations].sort((a, b) => a.timestampStart - b.timestampStart)[0];
  if (!o) return { t: run.duration / 2, caption: 'Mid-recording' };
  const evidence = o.evidence.find(e => e.kind === 'video') ?? o.evidence[0];
  return { t: evidence?.timestamp ?? (o.timestampStart + o.timestampEnd) / 2, stepId: o.stepId, caption: evidence?.description ?? o.summary ?? '' };
}
