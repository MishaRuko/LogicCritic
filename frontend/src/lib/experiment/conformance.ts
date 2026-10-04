import type { MethodContract, Observation, ProtocolRequirement, Run, VerificationResult } from './types';
export const MIN_CONFIDENCE = 0.8;
export function verify(requirement: ProtocolRequirement, observations: Observation[]): VerificationResult {
  const candidates = observations.filter(o => o.stepId === requirement.id).sort((a, b) => b.confidence - a.confidence);
  // Agent observations are judged check by check, so they are considered at any confidence; the confidence gate moves to accusations below.
  const o = candidates.find(v => v.confidence >= MIN_CONFIDENCE || v.checkResults);
  const evidence = candidates.flatMap(v => v.evidence);
  const unknown = (reason: string, missingEvidence: string[]): VerificationResult => ({ status: 'unverifiable', reason, missingEvidence: [...new Set(missingEvidence)], evidence });
  if (!o) return unknown(candidates.length ? 'Observation confidence is below the verification threshold.' : 'This requirement is not established by the recorded window.', requirement.requiredEvidence?.length ? requirement.requiredEvidence : requirement.checks?.length ? requirement.checks : ['video evidence of this step']);
  const conflict = (expected: unknown, observed: unknown): VerificationResult => ({ status: 'contradicted', confidence: o.confidence, expected, observed, evidence: o.evidence });
  if (o.checkResults) {
    // Agent observations report each methodology condition separately; the rule below still decides the state.
    // "confirmed" already means the frames clearly show it, so every check confirmed with evidence verifies the step.
    // An accusation needs more: a contradicted check counts only at MIN_CONFIDENCE, otherwise the step stays unverifiable.
    const deviation = o.checkResults.find(c => c.result === 'contradicted');
    if (deviation && o.confidence >= MIN_CONFIDENCE) return conflict(deviation.check, deviation.note);
    // Core checks decide whether the step was done; detail checks (manner, follow-through) only annotate a done step.
    const isDetail = (check: string) => requirement.details?.includes(check) ?? false;
    const confirmed = (check: string) => o.checkResults!.some(r => r.check === check && r.result === 'confirmed');
    const open = [...new Set([...o.checkResults.map(c => c.check), ...requirement.checks ?? []])].filter(c => !confirmed(c));
    const unresolved = open.filter(c => !isDetail(c));
    if (!o.evidence.length) unresolved.push('timestamped evidence');
    if (unresolved.length) return unknown('Part of this step is visible, but not every condition can be established from the recording.', unresolved);
    const unconfirmedDetails = open.filter(isDetail);
    return { status: 'verified', confidence: o.confidence, evidence: o.evidence, ...(unconfirmedDetails.length && { unconfirmedDetails }) };
  }
  const missing: string[] = [];
  // A mismatch only counts when the field is explicitly established by evidence.
  for (const field of ['action', 'source', 'target'] as const) {
    if (!requirement[field]) continue;
    if (!o.observed[field] || !o.establishes.includes(field)) missing.push(field);
    else if (o.observed[field] !== requirement[field]) return conflict({ [field]: requirement[field] }, { [field]: o.observed[field] });
  }
  if (requirement.duration) {
    const d = o.observed.durationSeconds;
    if (d === undefined || !o.establishes.includes('duration')) missing.push('continuous timing evidence');
    else {
      const { minSeconds, maxSeconds, expectedSeconds } = requirement.duration;
      if (d < (minSeconds ?? expectedSeconds ?? 0) || d > (maxSeconds ?? expectedSeconds ?? Infinity)) return conflict(requirement.duration, { durationSeconds: d });
    }
  }
  for (const [ids, relation] of [[requirement.after, 'after'], [requirement.before, 'before']] as const) {
    for (const id of ids ?? []) {
      const anchor = observations.find(a => a.stepId === id && a.confidence >= MIN_CONFIDENCE && a.establishes.includes('action'));
      if (!anchor) missing.push(`ordering anchor: ${id}`);
      else if (relation === 'after' ? o.timestampStart < anchor.timestampEnd : o.timestampEnd > anchor.timestampStart) return conflict({ [relation]: id }, { start: o.timestampStart, anchorStart: anchor.timestampStart, anchorEnd: anchor.timestampEnd });
    }
  }
  missing.push(...(requirement.requiredEvidence ?? []).filter(e => !o.establishes.includes(e)));
  if (!o.evidence.length) missing.push('timestamped evidence');
  if (missing.length) return unknown('Available evidence establishes part of this requirement, but cannot establish every required condition.', missing);
  return { status: 'verified', confidence: o.confidence, evidence: o.evidence };
}
export function verifyMethod(method: MethodContract, observations: Observation[]): Record<string, VerificationResult> {
  return Object.fromEntries(method.requirements.map(r => [r.id, verify(r, observations)]));
}
/** A wait longer than this is routinely cut from recordings, so its absence is never called a skip. */
export const LONG_WAIT_SECONDS = 60;
/** How far a step's start may precede an earlier step's start (or follow a later one's) before it is out of order. */
export const ORDER_TOLERANCE_SECONDS = 2;

/** Per-step verdicts, plus the sequence rules for agent runs, over the steps the agent located:
 * - out of order: a located step outside the longest run of steps located in protocol order, by more than the
 *   tolerance, flagged only at MIN_CONFIDENCE (it is an accusation);
 * - skipped: an unlocated step the agent watched for and saw not done, which is not a wait longer than
 *   LONG_WAIT_SECONDS. In a group of steps that look the same on camera, it is reported as "one of" the group.
 * Missing footage alone is still never a skip: the agent must have seen the stretch where the step belongs.
 */
export function verifyRun(method: MethodContract, run: Pick<Run, 'observations' | 'duration' | 'absences'>): Record<string, VerificationResult> {
  const results = verifyMethod(method, run.observations);
  const steps = [...method.requirements].sort((a, b) => a.order - b.order);
  const placed = steps.map(r => run.observations
    .filter(o => o.stepId === r.id && o.provenance === 'claude_agent')
    .sort((a, b) => b.confidence - a.confidence)[0]);
  const inOrder = longestInOrder(placed);
  const ordered = placed.map((o, i) => o && (inOrder.has(i) ? o : undefined));
  steps.forEach((r, i) => {
    const o = placed[i];
    if (!o || inOrder.has(i)) return;
    const late = ordered.slice(0, i).find(a => a && a.timestampStart > o.timestampStart + ORDER_TOLERANCE_SECONDS);
    const early = ordered.slice(i + 1).find(a => a && a.timestampStart < o.timestampStart - ORDER_TOLERANCE_SECONDS);
    const anchor = late ?? early;
    if (!anchor) { ordered[i] = o; return; }
    if (results[r.id].status === 'contradicted' || o.confidence < MIN_CONFIDENCE) return;
    results[r.id] = { status: 'contradicted', confidence: o.confidence, expected: `${late ? 'after' : 'before'} ${anchor.stepId}`, observed: `started at ${o.timestampStart.toFixed(1)} s; ${anchor.stepId} started at ${anchor.timestampStart.toFixed(1)} s`, evidence: o.evidence };
  });
  for (const absence of run.absences ?? []) {
    const i = steps.findIndex(r => r.id === absence.stepId);
    if (i < 0 || absence.reason !== 'not_performed' || waitSeconds(steps[i].description) > LONG_WAIT_SECONDS || run.observations.some(o => o.stepId === absence.stepId)) continue;
    const prev = ordered.slice(0, i).filter(Boolean).at(-1), next = ordered.slice(i + 1).find(Boolean);
    const between: [number, number] = [prev?.timestampEnd ?? 0, next?.timestampStart ?? run.duration];
    const group = steps[i].visualGroup ? steps.filter(r => r.visualGroup === steps[i].visualGroup) : [];
    const reason = group.length > 1 ? `One of ${group.map(r => r.title).join(' / ')} was not performed (they look the same on camera, so which one cannot be told). ${absence.note}` : absence.note;
    results[absence.stepId] = { status: 'skipped', confidence: absence.confidence, reason, between, evidence: [...prev?.evidence ?? [], ...next?.evidence ?? []] };
  }
  return results;
}

/** The longest wait a step's wording asks for, in seconds ("incubate 20 min" -> 1200); 0 if it states none. */
export function waitSeconds(text: string): number {
  const units: Record<string, number> = { s: 1, sec: 1, secs: 1, second: 1, seconds: 1, min: 60, mins: 60, minute: 60, minutes: 60, h: 3600, hr: 3600, hrs: 3600, hour: 3600, hours: 3600 };
  return Math.max(0, ...[...text.matchAll(/(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|hours?|hrs?|hr|h)\b/gi)].map(m => Number(m[1]) * units[m[2].toLowerCase()]));
}

/** Indices of the longest subsequence whose start times never decrease in protocol order. */
function longestInOrder(placed: (Observation | undefined)[]): Set<number> {
  const idx = placed.flatMap((o, i) => o ? [i] : []);
  const length = idx.map(() => 1), from = idx.map(() => -1);
  idx.forEach((i, k) => { for (let j = 0; j < k; j++) if (placed[idx[j]]!.timestampStart <= placed[i]!.timestampStart && length[j] + 1 > length[k]) { length[k] = length[j] + 1; from[k] = j; } });
  const keep = new Set<number>();
  for (let k = length.indexOf(Math.max(0, ...length)); k >= 0; k = from[k]) keep.add(idx[k]);
  return keep;
}

const weights = { informational: 1, important: 2, critical: 3 };
export function evidenceDebt(method: MethodContract, results: Record<string, VerificationResult>) {
  const totalWeight = method.requirements.reduce((n, r) => n + weights[r.criticality], 0);
  const categories: Record<string, number> = { identity: 0, timing: 0, action: 0, ordering: 0 };
  let unresolvedWeight = 0;
  for (const r of method.requirements) if (!results[r.id] || results[r.id].status === 'unverifiable') { unresolvedWeight += weights[r.criticality]; categories[r.category] += weights[r.criticality]; }
  return { percent: totalWeight ? unresolvedWeight / totalWeight * 100 : 0, totalWeight, unresolvedWeight, categories: Object.fromEntries(Object.entries(categories).map(([k, v]) => [k, totalWeight ? v / totalWeight * 100 : 0])) };
}
