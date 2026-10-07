import type { VerificationEvent } from '../../types/api';

export const verificationLabels: Record<string, string> = {
  ungrounded_statement: 'Source grounding',
  missing_premise: 'Required premises',
  causality_overclaim: 'Causal support',
  scope_leap: 'Scope and extrapolation',
  direct_conflict: 'Conflicting claims',
  invalidated_source: 'Source validity',
  reported_limitation: 'Reported limitations',
};
export type VerificationTrace = {
  workspaceId: string;
  status: 'running' | 'complete' | 'failed';
  events: VerificationEvent[];
};

export function verificationHighlights(trace?: VerificationTrace) {
  const active = new Set<string>();
  const checked = new Set<string>();
  const attention = new Set<string>();
  for (const event of trace?.events ?? []) {
    if (event.type === 'rule_started') {
      active.clear();
      event.node_ids.forEach(id => active.add(id));
    }
    if (event.type === 'rule_completed') {
      active.clear();
      event.node_ids.forEach(id => checked.add(id));
      event.findings.forEach(finding => attention.add(finding.node_id));
    }
  }
  if (trace?.status !== 'running') active.clear();
  return { active, checked, attention };
}
