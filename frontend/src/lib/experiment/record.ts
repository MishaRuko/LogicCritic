import { evidenceDebt, verifyMethod } from './conformance';
import { sha256 } from './hashing';
import type {
  ExperimentRecord,
  MethodContract,
  RecordSnapshot,
  Run,
  VerificationResult,
} from './types';
export async function createRecord(
  run: Run,
  method: MethodContract,
  snapshot?: RecordSnapshot,
  suppliedResults?: Record<string, VerificationResult>,
  rawObservations?: unknown[],
): Promise<ExperimentRecord> {
  const results = suppliedResults ?? verifyMethod(method, run.observations);
  const agent = !run.subtitle.startsWith('Synthetic');
  const body = {
    schemaVersion: 2 as const,
    experimentId: run.id,
    generatedAt: new Date().toISOString(),
    method,
    methodHash: await sha256(method),
    run,
    results,
    evidenceDebt: evidenceDebt(method, results),
    observationHash: await sha256(run.observations),
    limitations: [
      agent
        ? `${run.subtitle}. Observations come from sampled video frames; they cannot establish continuous durations or unreadable labels.`
        : 'These observations are synthetic demo inputs. No recording was analysed.',
      `Verification states are decided by deterministic rules over the reported observations; observations below the ${run.observations.some(o => o.provenance === 'claude_agent') ? 'video-agent confidence threshold of 0.7' : 'lab-vision confidence threshold of 0.6'} do not verify a step.`,
      'Hashes establish integrity of the method and record; they do not prove that a physical event occurred.',
      'Confidence is an estimate, not a calibrated probability.',
      'Unverifiable steps in a partial recording may be outside the recorded excerpt. They are not evidence of an omission.',
    ],
    ...(snapshot && { snapshot }),
    ...(rawObservations && { rawObservations }),
  };
  return { ...body, recordHash: await sha256(body) };
}
export async function validateRecord(record: ExperimentRecord): Promise<boolean> {
  const { recordHash, ...body } = record;
  return (
    recordHash === (await sha256(body)) &&
    record.methodHash === (await sha256(record.method)) &&
    record.observationHash === (await sha256(record.run.observations))
  );
}
/** Publishes a record to the Worker and returns its shareable URL. Content-addressed, so republishing is idempotent. */
export async function publishRecord(record: ExperimentRecord): Promise<string> {
  const response = await fetch('/api/records', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(record),
  });
  const body = (await response.json().catch(() => ({}))) as { path?: string; error?: string };
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
