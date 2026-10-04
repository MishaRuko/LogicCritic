export type Status = 'verified' | 'contradicted' | 'unverifiable';
export type EvidenceCategory = 'identity' | 'timing' | 'action' | 'ordering';
export type ProtocolRequirement = {
  id: string; title: string; description: string; order: number;
  /** The source sentence(s), verbatim, so each step traces back to the document. */
  quote?: string;
  action?: string; source?: string; target?: string;
  duration?: { expectedSeconds?: number; minSeconds?: number; maxSeconds?: number };
  after?: string[]; before?: string[]; requiredEvidence?: string[];
  /** Visually checkable conditions extracted from the methodology; all must be confirmed to verify the step. */
  checks?: string[];
  /** Supporting visual details: reported, and a contradiction still counts, but they do not block verification. */
  details?: string[];
  definingAction?: string;
  visualGroup?: string;
  /** Conditions the methodology states that a recording cannot establish (sterility, temperature…). */
  caveats?: string[];
  criticality: 'informational' | 'important' | 'critical'; category: EvidenceCategory;
};
export type MethodContract = { schemaVersion: 1; title: string; version: string; source: string; summary?: string; requirements: ProtocolRequirement[] };
export type Evidence = { id: string; timestamp: number; end: number; description: string; kind: 'video' | 'dataset_annotation'; frame?: string };
export type CheckResult = { check: string; result: 'confirmed' | 'contradicted' | 'not_visible'; note: string; required?: boolean };
export type Observation = {
  id: string; stepId: string; timestampStart: number; timestampEnd: number;
  observed: { action?: string; source?: string; target?: string; durationSeconds?: number };
  evidence: Evidence[]; establishes: string[]; uncertain: string[]; confidence: number;
  provenance: 'annotation_assisted_visual_review' | 'workers_ai' | 'claude_agent' | 'lab_vision' | 'synthetic' | 'saved_model_analysis';
  /** Agent observations: what happened, checked condition by condition. */
  summary?: string; checkResults?: CheckResult[];
  /** The recording continuously shows the window where this step had to happen, and it is not performed there. */
  omitted?: boolean;
  /** Steps the agent saw performed at the same time (background waits, two hands, interleaving). */
  concurrentWith?: string[];
};
export type VerificationResult =
  | { status: 'verified'; confidence: number; evidence: Evidence[] }
  | { status: 'contradicted'; confidence: number; expected: unknown; observed: unknown; evidence: Evidence[] }
  | { status: 'unverifiable'; reason: string; missingEvidence: string[]; evidence: Evidence[] };
export type MediaProvenance = { sourceRevision: string; sourceSha256: string; cachedSha256: string; sourceUrl: string; originalFilename: string; transform: string };
export type Run = {
  id: string; title: string; subtitle: string; date: string; video: string; duration: number; poster?: string;
  observations: Observation[]; sourceClip?: string; mediaProvenance?: MediaProvenance;
};
export type ExperimentRecord = {
  schemaVersion: 2; experimentId: string; generatedAt: string; method: MethodContract; methodHash: string;
  run: Run; results: Record<string, VerificationResult>;
  evidenceDebt: ReturnType<typeof import('./conformance').evidenceDebt>;
  observationHash: string; recordHash: string; limitations: string[];
  /** A still from the recording at the record's key moment (JPEG data URL). */
  snapshot?: RecordSnapshot;
  rawObservations?: unknown[];
};
export type RecordSnapshot = { image: string; t: number; stepId?: string; caption: string };
