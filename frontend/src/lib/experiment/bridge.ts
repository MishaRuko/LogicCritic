import type { ExperimentProtocol, ExperimentRun, SourceWithExcerpts } from '../../types/api';
import type { Evidence, MethodContract, Observation, Run, VerificationResult } from './types';

export function experimentPresentation(job: ExperimentRun, protocol: ExperimentProtocol, source?: SourceWithExcerpts, duration?: number) {
  if (job.result.agent_method && job.result.agent_observations && job.result.agent_results) {
    const method = job.result.agent_method;
    const run: Run = {
      id: job.id, title: method.title, subtitle: job.result.coverage === 'excerpt' ? 'Video inspection · partial recording' : 'Video inspection',
      date: job.created_at.slice(0, 10), video: `/api/experiment-runs/${job.id}/recording`,
      duration: duration ?? job.result.duration ?? 1, observations: job.result.agent_observations,
      poster: job.result.overview?.[0] ? `data:image/jpeg;base64,${job.result.overview[0].data}` : undefined,
    };
    return { method, run, results: job.result.agent_results };
  }
  const recorded = job.result.observations ?? [];
  const findings = job.result.deviations ?? [];
  const partial = job.result.coverage === 'excerpt';
  const method: MethodContract = {
    schemaVersion: 1, title: protocol.protocol.title, version: protocol.protocol.version,
    source: source?.original_filename ?? protocol.source_id,
    summary: 'Extracted from the research; each step retains its source passage.',
    requirements: protocol.protocol.steps.map((step, index) => ({
      id: step.id, order: index + 1, title: step.description.split(/\n|\.\s/)[0].split(' ').slice(0, 9).join(' '),
      description: step.description, quote: step.source_text, checks: step.checks.map(c => `${c.question} Expected: ${c.expected}${c.unit ? ` ${c.unit}` : ''}${c.tolerance ? ` ± ${c.tolerance}` : ''}`),
      criticality: 'informational', category: 'action',
    })),
  };
  const allObservations: Observation[] = recorded.filter(o => !!o.step_id && method.requirements.some(r => r.id === o.step_id)).map(o => {
    const step = protocol.protocol.steps.find(s => s.id === o.step_id)!;
    const local = findings.filter(d => d.step_id === o.step_id);
    return {
      id: o.id, stepId: o.step_id!, timestampStart: o.span.start_s, timestampEnd: o.span.end_s,
      observed: { action: step.description }, establishes: o.status === 'performed' ? ['action'] : [],
      uncertain: o.status === 'performed' ? [] : ['action'], confidence: o.confidence,
      provenance: job.mode === 'demo' ? 'synthetic' : job.mode === 'replay' ? 'saved_model_analysis' : 'lab_vision', summary: o.description ?? o.status,
      evidence: [{ id: o.id, kind: 'video', timestamp: o.span.start_s, end: o.span.end_s, description: o.description ?? o.status }],
      checkResults: [
        { check: 'Documented action', result: o.status === 'performed' ? 'confirmed' : 'not_visible', note: o.description ?? o.status },
        ...step.checks.map(check => {
          const issue = local.find(d => d.check_id === check.id);
          const value = o.values.find(v => v.check_id === check.id);
          return { check: check.question, result: issue && !issue.needs_review ? 'contradicted' as const : issue || value?.value == null || (value?.confidence ?? 0) < .6 ? 'not_visible' as const : 'confirmed' as const, note: issue?.message ?? `${value?.value ?? 'Unreadable'}${check.unit ? ` ${check.unit}` : ''}; expected ${check.expected}` };
        }),
      ],
    };
  });
  // The reference UI uses one located window per step. Retain every source observation in the report separately.
  const observations = method.requirements.flatMap(requirement => {
    const candidates = allObservations.filter(o => o.stepId === requirement.id);
    const located = candidates.filter(o => o.confidence >= .3);
    const best = [...located].sort((a, b) => Number(recorded.find(o => o.id === b.id)?.status === 'performed') - Number(recorded.find(o => o.id === a.id)?.status === 'performed') || b.confidence - a.confidence)[0];
    return best ? [{ ...best, evidence: located.flatMap(o => o.evidence) }] : [];
  });
  const results: Record<string, VerificationResult> = Object.fromEntries(method.requirements.map(requirement => {
    const candidates = observations.filter(o => o.stepId === requirement.id).sort((a, b) => b.confidence - a.confidence);
    const evidence: Evidence[] = candidates.flatMap(o => o.evidence);
    const deviations = findings.filter(d => d.step_id === requirement.id);
    const conflict = deviations.find(d => !d.needs_review);
    if (conflict) return [requirement.id, { status: 'contradicted', confidence: candidates[0]?.confidence ?? .6, expected: conflict.expected ?? requirement.description, observed: conflict.observed ?? conflict.message, evidence } satisfies VerificationResult];
    const complete = candidates.find(o => o.confidence >= .6 && recorded.find(raw => raw.id === o.id)?.status === 'performed');
    const unresolved = deviations.filter(d => d.needs_review).map(d => d.message);
    if (complete && !unresolved.length && complete.checkResults?.every(c => c.result === 'confirmed')) return [requirement.id, { status: 'verified', confidence: complete.confidence, evidence } satisfies VerificationResult];
    const reason = unresolved[0] ?? (candidates.length ? 'The recording does not establish the complete action.' : partial ? 'Outside the recorded excerpt; this step was not assessed.' : 'This step was not located in the recording.');
    return [requirement.id, { status: 'unverifiable', reason, missingEvidence: unresolved.length ? unresolved : [reason], evidence } satisfies VerificationResult];
  }));
  const sample = job.filename === 'DJI_08-first-30s.observations.jsonl';
  const run: Run = {
    id: job.id, title: method.title,
    subtitle: job.mode === 'demo' ? 'Synthetic observations' : sample ? 'Saved sample analysis · partial recording' : partial ? 'Video analysis · partial recording' : 'Video analysis',
    date: job.created_at.slice(0, 10),
    video: job.mode === 'video' ? `/api/experiment-runs/${job.id}/recording` : sample ? '/demo/lsv/DJI_08-first-30s.mp4' : '',
    duration: Math.max(1, duration ?? (sample ? 30 : Math.max(0, ...recorded.map(o => o.span.end_s)))),
    observations, poster: sample ? '/demo/lsv/DJI_08-preview.jpg' : undefined,
  };
  return { method, run, results };
}
