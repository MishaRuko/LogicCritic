import type { MethodContract, Observation, ProtocolRequirement, VerificationResult } from './types';
/** Below this, an observation is never verified or contradicted. Set from the LSV evaluation: the agent rarely reports above 0.75 even when every required check is confirmed. */
export const MIN_CONFIDENCE = 0.7;
export function verify(
  requirement: ProtocolRequirement,
  observations: Observation[],
): VerificationResult {
  const candidates = observations
    .filter(o => o.stepId === requirement.id)
    .sort((a, b) => b.confidence - a.confidence);
  const o = candidates.find(v => v.confidence >= MIN_CONFIDENCE);
  const evidence = candidates.flatMap(v => v.evidence);
  const unknown = (reason: string, missingEvidence: string[]): VerificationResult => ({
    status: 'unverifiable',
    reason,
    missingEvidence: [...new Set(missingEvidence)],
    evidence,
  });
  if (!o)
    return unknown(
      candidates.length
        ? 'Observation confidence is below the verification threshold.'
        : 'This requirement is not established by the recorded window.',
      requirement.requiredEvidence?.length
        ? requirement.requiredEvidence
        : requirement.checks?.length
          ? requirement.checks
          : ['video evidence of this step'],
    );
  const conflict = (expected: unknown, observed: unknown): VerificationResult => ({
    status: 'contradicted',
    confidence: o.confidence,
    expected,
    observed,
    evidence: o.evidence,
  });
  // An omission only reaches here when the agent reported the step's whole window as watched and the step absent.
  if (o.omitted)
    return conflict(
      `${requirement.title}: performed`,
      o.summary ?? 'Not performed while the recording shows the window where it belongs.',
    );
  if (o.checkResults) {
    // Agent observations report each methodology condition separately; the rule below still decides the state.
    // Any contradicted check is a deviation; only required checks must be confirmed for the step to verify.
    const deviation = o.checkResults.find(c => c.result === 'contradicted');
    if (deviation) return conflict(deviation.check, deviation.note);
    const unresolved = o.checkResults
      .filter(c => c.required !== false && c.result !== 'confirmed')
      .map(c => c.check);
    const unchecked = (requirement.checks ?? []).filter(
      c => !o.checkResults!.some(r => r.check === c),
    );
    if (!o.evidence.length) unresolved.push('timestamped evidence');
    if (unresolved.length || unchecked.length)
      return unknown(
        'Part of this step is visible, but not every condition can be established from the recording.',
        [...unresolved, ...unchecked],
      );
    return { status: 'verified', confidence: o.confidence, evidence: o.evidence };
  }
  const missing: string[] = [];
  // A mismatch only counts when the field is explicitly established by evidence.
  for (const field of ['action', 'source', 'target'] as const) {
    if (!requirement[field]) continue;
    if (!o.observed[field] || !o.establishes.includes(field)) missing.push(field);
    else if (o.observed[field] !== requirement[field])
      return conflict({ [field]: requirement[field] }, { [field]: o.observed[field] });
  }
  if (requirement.duration) {
    const d = o.observed.durationSeconds;
    if (d === undefined || !o.establishes.includes('duration'))
      missing.push('continuous timing evidence');
    else {
      const { minSeconds, maxSeconds, expectedSeconds } = requirement.duration;
      if (
        d < (minSeconds ?? expectedSeconds ?? 0) ||
        d > (maxSeconds ?? expectedSeconds ?? Infinity)
      )
        return conflict(requirement.duration, { durationSeconds: d });
    }
  }
  for (const [ids, relation] of [
    [requirement.after, 'after'],
    [requirement.before, 'before'],
  ] as const) {
    for (const id of ids ?? []) {
      const anchor = observations.find(
        a => a.stepId === id && a.confidence >= MIN_CONFIDENCE && a.establishes.includes('action'),
      );
      if (!anchor) missing.push(`ordering anchor: ${id}`);
      else if (
        relation === 'after'
          ? o.timestampStart < anchor.timestampEnd
          : o.timestampEnd > anchor.timestampStart
      )
        return conflict(
          { [relation]: id },
          {
            start: o.timestampStart,
            anchorStart: anchor.timestampStart,
            anchorEnd: anchor.timestampEnd,
          },
        );
    }
  }
  missing.push(...(requirement.requiredEvidence ?? []).filter(e => !o.establishes.includes(e)));
  if (!o.evidence.length) missing.push('timestamped evidence');
  if (missing.length)
    return unknown(
      'Available evidence establishes part of this requirement, but cannot establish every required condition.',
      missing,
    );
  return { status: 'verified', confidence: o.confidence, evidence: o.evidence };
}
export function verifyMethod(
  method: MethodContract,
  observations: Observation[],
): Record<string, VerificationResult> {
  return Object.fromEntries(method.requirements.map(r => [r.id, verify(r, observations)]));
}
const weights = { informational: 1, important: 2, critical: 3 };
export function evidenceDebt(method: MethodContract, results: Record<string, VerificationResult>) {
  const totalWeight = method.requirements.reduce((n, r) => n + weights[r.criticality], 0);
  const categories: Record<string, number> = { identity: 0, timing: 0, action: 0, ordering: 0 };
  let unresolvedWeight = 0;
  for (const r of method.requirements)
    if (!results[r.id] || results[r.id].status === 'unverifiable') {
      unresolvedWeight += weights[r.criticality];
      categories[r.category] += weights[r.criticality];
    }
  return {
    percent: totalWeight ? (unresolvedWeight / totalWeight) * 100 : 0,
    totalWeight,
    unresolvedWeight,
    categories: Object.fromEntries(
      Object.entries(categories).map(([k, v]) => [k, totalWeight ? (v / totalWeight) * 100 : 0]),
    ),
  };
}
