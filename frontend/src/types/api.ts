export interface Workspace {
  id: string;
  title: string;
  created_at: string;
}
export interface Provenance {
  actor_type: 'user' | 'agent' | 'extractor' | 'rule_engine' | 'integration';
  actor_id: string;
  model?: string;
  prompt_version?: string;
  run_id?: string;
}
export type Lifecycle = 'proposed' | 'accepted' | 'rejected';
export type AssertionMode = 'asserted' | 'hypothesis' | 'conditional' | 'question' | 'reported';
export type StatementRole = 'premise' | 'conclusion' | 'assumption' | 'objection' | 'definition';
export interface Statement {
  id: string;
  workspace_id: string;
  text: string;
  assertion_mode: AssertionMode;
  role: StatementRole | null;
  salience: 'core' | 'secondary' | 'supporting';
  lifecycle: Lifecycle;
  provenance: Provenance;
  superseded_by?: string | null;
  created_at: string;
  excerpt_ids: string[];
}
export interface ReasoningStep {
  id: string;
  workspace_id: string;
  conclusion_id: string;
  premise_ids: string[];
  explanation: string;
  lifecycle: Lifecycle;
  provenance: Provenance;
  created_at: string;
}
export interface Relation {
  id: string;
  source_node_kind: string;
  source_node_id: string;
  target_node_kind: string;
  target_node_id: string;
  relation: string;
  metadata: Record<string, unknown>;
}
export interface Graph {
  statements: Statement[];
  reasoning_steps: ReasoningStep[];
  relations: Relation[];
}
export interface Source {
  id: string;
  workspace_id: string;
  title: string | null;
  original_filename: string;
  kind: string;
  origin: string;
  mime_type: string;
  content_hash: string;
  external_ids: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
}
export interface Excerpt {
  id: string;
  source_id: string;
  text: string;
  locator: Record<string, unknown>;
  sequence: number;
  created_at: string;
}
export interface SourceWithExcerpts extends Source {
  excerpts: Excerpt[];
}
export interface Job {
  id: string;
  workspace_id: string;
  source_id: string;
  model: string;
  status: string;
  attempts: number;
  total_chunks: number;
  completed_chunks: number;
  output_patch_ids: string[];
  error: string | null;
  next_attempt_at: string | null;
  heartbeat_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}
export interface Obligation {
  id: string;
  kind: string;
  description: string;
  required_condition: string;
  blocks_statement_id: string | null;
  blocks_node_type: string | null;
  blocks_node_id: string | null;
  generated_by_rule: string | null;
  status: string;
  created_at: string;
}
export interface Issue {
  id: string;
  rule_code: string;
  node_type: string;
  node_id: string;
  details: Record<string, unknown>;
  status: string;
  created_at: string;
}
export interface Context {
  focus_statement: Statement;
  upstream_statements: Statement[];
  downstream_statements: Statement[];
  reasoning_steps: ReasoningStep[];
  obligations: Obligation[];
  issues: Issue[];
}
export interface Verification {
  verification_event_id: string;
  rules_run: string[];
  issues_opened: number;
  issues_resolved: number;
  obligations_opened: number;
  obligations_resolved: number;
}
export type VerificationEvent =
  | { type: 'started'; rules: string[] }
  | { type: 'rule_started'; rule_code: string; node_ids: string[] }
  | {
      type: 'rule_completed';
      rule_code: string;
      node_ids: string[];
      findings: { node_id: string; node_type: string; message: string }[];
    }
  | { type: 'completed'; result: Verification }
  | { type: 'error'; message: string };
export interface Validity {
  id: string;
  source_id: string;
  status: 'valid' | 'invalidated';
  reason: string;
  provenance: Provenance;
  created_at: string;
}
export type PatchOperation =
  | {
      op: 'create_statement';
      client_ref: string;
      text: string;
      assertion_mode: AssertionMode;
      role?: StatementRole;
      salience?: 'core' | 'secondary' | 'supporting';
      excerpt_ids: string[];
      provenance: Provenance;
      lifecycle?: 'proposed';
    }
  | {
      op: 'create_reasoning_step';
      client_ref: string;
      premise_ids: string[];
      conclusion_id: string;
      explanation: string;
      provenance: Provenance;
      lifecycle?: 'proposed';
    }
  | {
      op: 'create_relation';
      source_node_kind: 'statement' | 'reasoning_step';
      source_node_id: string;
      relation: 'supports' | 'rebuts' | 'undercuts' | 'qualifies' | 'specializes' | 'revises';
      target_node_kind: 'statement' | 'reasoning_step';
      target_node_id: string;
      metadata: Record<string, unknown>;
    }
  | {
      op: 'create_annotation';
      subject_type: 'statement' | 'reasoning_step';
      subject_id: string;
      type: string;
      value: Record<string, unknown>;
      provenance: Provenance;
      confidence?: number;
      status?: 'proposed';
    };
export interface Snapshot {
  experiments?: Experiments;
  workspace: Workspace;
  graph: Graph;
  contexts: Context[];
  sources: SourceWithExcerpts[];
  jobs: Job[];
  validity: Record<string, Validity>;
}

export interface ProtocolCheck {
  id: string;
  kind: 'numeric' | 'equals';
  question: string;
  expected: string | number;
  unit: string | null;
  tolerance: number;
}
export interface ProtocolStep {
  id: string;
  description: string;
  source_text: string;
  optional: boolean;
  checks: ProtocolCheck[];
}
export interface ExperimentProtocol {
  id: string;
  source_id: string;
  protocol: { id: string; title: string; version: string; steps: ProtocolStep[] };
  step_excerpts: Record<string, string[]>;
  extraction_method: string;
  approved_at: string | null;
  current?: boolean;
  created_at: string;
}
export interface ExperimentObservation {
  id: string;
  step_id: string | null;
  status: string;
  confidence: number;
  description: string | null;
  values: { check_id: string; value: string | number | null; confidence: number }[];
  span: { start_s: number; end_s: number };
}
export interface ExperimentDeviation {
  id: string;
  kind: string;
  step_id: string | null;
  check_id: string | null;
  message: string;
  needs_review: boolean;
  expected: string | number | null;
  observed: string | number | null;
  span: { start_s: number; end_s: number } | null;
}
export interface VideoAgentEvent {
  kind: 'overview' | 'inspect' | 'retry' | 'done';
  start?: number;
  end?: number;
  count?: number;
  frames?: number;
  message?: string;
}
export interface ExperimentRun {
  id: string;
  protocol_id: string;
  mode: 'demo' | 'replay' | 'video';
  status: string;
  filename: string;
  result: {
    coverage?: 'excerpt' | 'complete_recording';
    processed_seconds?: number;
    source_id?: string;
    observations?: ExperimentObservation[];
    deviations?: ExperimentDeviation[];
    analysis_stage?: 'method' | 'frames' | 'inspection' | 'verification' | 'done';
    duration?: number;
    overview?: { t: number; data: string }[];
    agent_events?: VideoAgentEvent[];
    agent_method?: import('../lib/experiment/types').MethodContract;
    agent_observations?: import('../lib/experiment/types').Observation[];
    agent_results?: Record<string, import('../lib/experiment/types').VerificationResult>;
    summary?: {
      observations: number;
      deviations: number;
      needs_review: number;
      failed_windows: number;
    };
  };
  error: string | null;
  created_at: string;
  completed_at: string | null;
}
/** The protocol the latest finished agent run handed over (null if that run handed over none). */
export interface SuggestedProtocol {
  run_id: string;
  source_id: string;
  protocol_id: string | null;
  current: boolean;
}
export interface Experiments {
  verified: boolean;
  verification?: Verification | null;
  protocols: ExperimentProtocol[];
  runs: ExperimentRun[];
  suggested?: SuggestedProtocol | null;
}

// -- Research agent ---------------------------------------------------------------------------
// Endpoints: POST /workspaces/{id}/agent-runs, GET /agent-runs/{id}, GET /agent-runs/{id}/events?after=,
// GET /agent-runs/{id}/verdicts, GET /workspaces/{id}/agent-runs, DELETE /agent-runs/{id}.
export type AgentKind = 'question' | 'claim' | 'hypothesis';
export type AgentStatus =
  'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'budget_exhausted';
export type AgentCertainty = 'established' | 'conditional' | 'hypothesis' | 'abstained';
export interface AgentRunCreate {
  idempotency_key: string;
  question: string;
  kind?: AgentKind;
  completion_criteria?: string[];
  falsifiers?: string[];
  mode?: 'guarded' | 'baseline';
  model?: string;
  max_turns?: number;
  max_web_searches?: number;
}
export interface AgentGoal {
  id: string;
  question: string;
  kind: AgentKind;
  completion_criteria: string[];
  falsifiers: string[];
  status: string;
}

/** How settled the answer is, as a named level (never a number). `scale` is ordered from most to
 *  least uncertain: draw one segment per entry and fill up to `level`. */
export type AssuranceLevel =
  'unexplored' | 'exploring' | 'contested' | 'provisional' | 'well_supported' | 'settled';
export interface Assurance {
  level: AssuranceLevel;
  label: string;
  scale: AssuranceLevel[];
  holding_back: { kind: string; description: string }[];
}

export interface AgentUsage {
  turns?: number;
  input_tokens?: number;
  output_tokens?: number;
  web_searches?: number;
  cost_usd?: number | null;
  judge?: { calls: number; input_tokens: number; output_tokens: number };
  judge_cost_usd?: number | null;
  assurance?: Assurance | null;
}
export interface AgentProtocol {
  source_id: string;
  title: string | null;
  basis: string;
  steps: { n: number; action: string; excerpt_ids: string[] }[];
  experiment_protocol_id?: string | null;
}
export interface AgentRun {
  id: string;
  workspace_id: string;
  goal_id: string;
  goal?: AgentGoal | null;
  protocol?: AgentProtocol | null;
  question: string;
  kind: AgentKind;
  mode: 'guarded' | 'baseline';
  model: string;
  status: AgentStatus;
  budgets: { max_turns: number; max_web_searches: number; max_total_output_tokens: number };
  usage: AgentUsage;
  error: string | null;
  final_report: string | null;
  final_statement_id: string | null;
  certainty: AgentCertainty | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface GuardObligation {
  kind: string;
  severity: 'critical' | 'advisory';
  description: string;
  required_condition: string;
  applies_to: string;
}
export interface CheckPacket {
  conclusion: { statement_id: string; text: string };
  evidence_chain: {
    statement_id: string;
    text: string;
    role: StatementRole | null;
    from_sources: {
      source_id: string;
      title: string | null;
      retracted: boolean;
      doi?: string;
      pmid?: string;
      url?: string;
    }[];
  }[];
  reasoning_steps: { step_id: string; premises: string[]; explanation: string }[];
  obligations: GuardObligation[];
  completion_criteria: [number, string][];
  can_finalize_as: ('established' | 'conditional' | 'hypothesis')[];
  assurance: Assurance;
  notes: string[];
  you_may: string[];
}
export type GraphChange =
  | {
      change: 'claim_added';
      statement_id: string;
      text: string;
      role: StatementRole | null;
      claim_strength: string | null;
      excerpt_ids: string[];
      position: boolean;
    }
  | {
      change: 'step_added';
      step_id: string;
      premise_ids: string[];
      conclusion_id: string;
      revises_step_id: string | null;
    }
  | {
      change: 'claim_superseded' | 'claim_withdrawn';
      statement_id: string;
      replaced_by_id: string | null;
      reason: string;
      position: boolean;
    };

/** One entry of a run's trace. Poll GET /agent-runs/{id}/events?after={seq}. */
export type KnownAgentEvent = { seq: number; created_at: string } & (
  | { type: 'run_started'; payload: { mode: string; model: string; budgets: AgentRun['budgets'] } }
  | {
      type: 'criteria_given' | 'criteria_proposed' | 'criteria_default';
      payload: { criteria: string[]; falsifiers?: string[] };
    }
  | {
      type: 'turn';
      payload: {
        turn: number;
        stop_reason: string;
        model: string | null;
        latency_s: number;
        usage: {
          input_tokens: number;
          output_tokens: number;
          cache_read_tokens: number;
          cache_write_tokens: number;
          web_searches: number;
        };
      };
    }
  | { type: 'thinking'; payload: { text: string | null; redacted?: boolean } }
  | { type: 'assistant_text'; payload: { text: string } }
  | { type: 'web_search'; payload: { query: string | null; tool_use_id: string } }
  | {
      type: 'web_results';
      payload: { results: { url: string | null; title: string | null }[]; error?: string };
    }
  | {
      type: 'tool_call';
      payload: { tool_use_id: string; name: string; input: Record<string, unknown> };
    }
  | {
      type: 'tool_result';
      payload: {
        tool_use_id: string;
        name: string;
        result: Record<string, unknown>;
        is_error: boolean;
      };
    }
  | { type: 'graph_change'; payload: GraphChange }
  | { type: 'assurance'; payload: Assurance }
  | { type: 'experiment_ready'; payload: { source_id: string; protocol_id: string; steps: number } }
  | { type: 'experiment_not_ready'; payload: { source_id: string; reason: string } }
  | {
      type: 'protocol';
      payload: {
        source_id: string;
        title: string;
        basis: string;
        steps: { n: number; action: string; excerpt_ids: string[] }[];
      };
    }
  | {
      type: 'check';
      payload: { tool_use_id: string; name: string; result: CheckPacket; is_error: boolean };
    }
  | {
      type: 'finalization';
      payload: {
        tool_use_id: string;
        name: string;
        result: {
          accepted: boolean;
          certainty?: string;
          conclusion?: string;
          caveats?: GuardObligation[];
          reason?: string;
          obligations?: GuardObligation[];
        };
        is_error: boolean;
      };
    }
  | { type: 'nudge'; payload: { reason: string; count: number } }
  | { type: 'server_block'; payload: { block: Record<string, unknown> } }
  | {
      type: 'run_finished';
      payload: {
        status: AgentStatus;
        error: string | null;
        certainty: AgentCertainty | null;
        usage: AgentUsage;
      };
    }
);

/** GET /agent-runs/{id}/verdicts: the independent reviewer's judgements; the last is current. */
export interface AgentVerdict {
  id: string;
  created_at: string;
  criteria: {
    index: number;
    criterion: string;
    met: boolean;
    rationale: string;
    supporting_statement_ids: string[];
  }[];
  designs: { statement_id: string; design_shown: boolean; rationale: string }[];
  searches: string[];
}

export type AgentRunStatus = AgentStatus;
export interface AgentRunInput {
  question: string;
  kind: 'question' | 'claim' | 'hypothesis';
  mode: 'guarded' | 'baseline';
  completion_criteria: string[];
  falsifiers: string[];
  max_turns?: number;
  max_web_searches?: number;
}

// Preserve unknown future trace events for display and replay.
export interface AgentEvent {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

// -- Questions about the graph ------------------------------------------------------------------
// POST /workspaces/{id}/graph-questions: answered from the graph alone, with the nodes it rests on.
export interface GraphAnswer {
  answer: string;
  statement_ids: string[];
  step_ids: string[];
  model: string;
  usage: Record<string, unknown>;
}
export interface GraphQuestion {
  id: string;
  workspaceId: string;
  question: string;
  createdAt: string;
  status: 'pending' | 'answered' | 'failed';
  answer?: GraphAnswer;
  error?: string;
}
