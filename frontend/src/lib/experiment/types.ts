export type Status = 'verified' | 'contradicted' | 'skipped' | 'unverifiable';
export type EvidenceCategory = 'identity' | 'timing' | 'action' | 'ordering';
export type ProtocolRequirement = {
  id: string; title: string; description: string; order: number;
  action?: string; source?: string; target?: string;
  duration?: { expectedSeconds?: number; minSeconds?: number; maxSeconds?: number };
  after?: string[]; before?: string[]; requiredEvidence?: string[];
  /** Visually checkable conditions extracted from the methodology. */
  checks?: string[];
  /** The subset of checks about manner or follow-through; a step whose other (core) checks pass is done even if these are unseen. */
  details?: string[];
  /** The one visible action that tells this step apart from its neighbours. */
  definingAction?: string;
  /** Shared by steps that look the same on camera (e.g. three reagent additions); they can only be told apart by order and count. */
  visualGroup?: string;
  /** Conditions the methodology states that a recording cannot establish (sterility, temperature…). */
  caveats?: string[];
  criticality: 'informational' | 'important' | 'critical'; category: EvidenceCategory;
};
export type MethodContract = { schemaVersion: 1; title: string; version: string; source: string; summary?: string; requirements: ProtocolRequirement[] };
export type Evidence = { id: string; timestamp: number; end: number; description: string; kind: 'video' | 'dataset_annotation'; frame?: string };
export type CheckResult = { check: string; result: 'confirmed' | 'contradicted' | 'not_visible'; note: string };
export type Observation = {
  id: string; stepId: string; timestampStart: number; timestampEnd: number;
  observed: { action?: string; source?: string; target?: string; durationSeconds?: number };
  evidence: Evidence[]; establishes: string[]; uncertain: string[]; confidence: number;
  provenance: 'annotation_assisted_visual_review' | 'workers_ai' | 'claude_agent';
  /** Agent observations: what happened, checked condition by condition. */
  summary?: string; checkResults?: CheckResult[];
};
/** The agent's account of a step it could not locate: watched where it belongs and saw it not done, or could not see. */
export type Absence = { stepId: string; reason: 'not_performed' | 'not_visible'; confidence: number; note: string };
export type VerificationResult =
  /** unconfirmedDetails: detail checks that could not be seen; the step is still done. */
  | { status: 'verified'; confidence: number; evidence: Evidence[]; unconfirmedDetails?: string[] }
  | { status: 'contradicted'; confidence: number; expected: unknown; observed: unknown; evidence: Evidence[] }
  /** Inferred from absence: the neighbouring steps leave no room for it and the agent saw it not done there. */
  | { status: 'skipped'; confidence: number; reason: string; between: [number, number]; evidence: Evidence[] }
  | { status: 'unverifiable'; reason: string; missingEvidence: string[]; evidence: Evidence[] };
export type MediaProvenance = { sourceRevision: string; sourceSha256: string; cachedSha256: string; sourceUrl: string; originalFilename: string; transform: string };
export type Run = {
  id: string; title: string; subtitle: string; date: string; video: string; duration: number; poster?: string;
  observations: Observation[]; sourceClip?: string; mediaProvenance?: MediaProvenance;
  /** Agent runs: steps it could not locate, and why. */
  absences?: Absence[];
};
export type ExperimentRecord = {
  schemaVersion: 2 | 3; experimentId: string; generatedAt: string; method: MethodContract; methodHash: string;
  run: Run; results: Record<string, VerificationResult>;
  evidenceDebt: ReturnType<typeof import('./conformance').evidenceDebt>;
  observationHash: string; recordHash: string; limitations: string[];
  /** A still from the recording at the record's key moment (JPEG data URL). */
  snapshot?: RecordSnapshot;
};
export type RecordSnapshot = { image: string; t: number; stepId?: string; caption: string };
