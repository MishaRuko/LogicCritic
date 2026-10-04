export interface Workspace { id: string; title: string; created_at: string }
export interface Provenance { actor_type: 'user' | 'agent' | 'extractor' | 'rule_engine' | 'integration'; actor_id: string; model?: string; prompt_version?: string; run_id?: string }
export type Lifecycle = 'proposed' | 'accepted' | 'rejected';
export type AssertionMode = 'asserted' | 'hypothesis' | 'conditional' | 'question' | 'reported';
export type StatementRole = 'premise' | 'conclusion' | 'assumption' | 'objection' | 'definition';
export interface Statement { id: string; workspace_id: string; text: string; assertion_mode: AssertionMode; role: StatementRole | null; salience: 'core' | 'supporting'; lifecycle: Lifecycle; provenance: Provenance; created_at: string; excerpt_ids: string[] }
export interface ReasoningStep { id: string; workspace_id: string; conclusion_id: string; premise_ids: string[]; explanation: string; lifecycle: Lifecycle; provenance: Provenance; created_at: string }
export interface Relation { id: string; source_node_kind: string; source_node_id: string; target_node_kind: string; target_node_id: string; relation: string; metadata: Record<string, unknown> }
export interface Graph { statements: Statement[]; reasoning_steps: ReasoningStep[]; relations: Relation[] }
export interface Source { id: string; workspace_id: string; title: string | null; original_filename: string; kind: string; origin: string; mime_type: string; content_hash: string; external_ids: Record<string, unknown>; metadata: Record<string, unknown>; created_at: string }
export interface Excerpt { id: string; source_id: string; text: string; locator: Record<string, unknown>; sequence: number; created_at: string }
export interface SourceWithExcerpts extends Source { excerpts: Excerpt[] }
export interface Job { id: string; workspace_id: string; source_id: string; model: string; status: string; attempts: number; total_chunks: number; completed_chunks: number; output_patch_ids: string[]; error: string | null; next_attempt_at: string | null; heartbeat_at: string | null; started_at: string | null; completed_at: string | null; created_at: string }
export interface Obligation { id: string; kind: string; description: string; required_condition: string; blocks_statement_id: string | null; blocks_node_type: string | null; blocks_node_id: string | null; generated_by_rule: string | null; status: string; created_at: string }
export interface Issue { id: string; rule_code: string; node_type: string; node_id: string; details: Record<string, unknown>; status: string; created_at: string }
export interface Context { focus_statement: Statement; upstream_statements: Statement[]; downstream_statements: Statement[]; reasoning_steps: ReasoningStep[]; obligations: Obligation[]; issues: Issue[] }
export interface Verification { verification_event_id: string; rules_run: string[]; issues_opened: number; issues_resolved: number; obligations_opened: number; obligations_resolved: number }
export interface Validity { id: string; source_id: string; status: 'valid' | 'invalidated'; reason: string; provenance: Provenance; created_at: string }
export type PatchOperation =
  | { op: 'create_statement'; client_ref: string; text: string; assertion_mode: AssertionMode; role?: StatementRole; salience?: 'core' | 'supporting'; excerpt_ids: string[]; provenance: Provenance; lifecycle?: 'proposed' }
  | { op: 'create_reasoning_step'; client_ref: string; premise_ids: string[]; conclusion_id: string; explanation: string; provenance: Provenance; lifecycle?: 'proposed' }
  | { op: 'create_relation'; source_node_kind: 'statement' | 'reasoning_step'; source_node_id: string; relation: 'supports' | 'rebuts' | 'undercuts' | 'qualifies' | 'specializes' | 'revises'; target_node_kind: 'statement' | 'reasoning_step'; target_node_id: string; metadata: Record<string, unknown> }
  | { op: 'create_annotation'; subject_type: 'statement' | 'reasoning_step'; subject_id: string; type: string; value: Record<string, unknown>; provenance: Provenance; confidence?: number; status?: 'proposed' };
export interface Snapshot { workspace: Workspace; graph: Graph; contexts: Context[]; sources: SourceWithExcerpts[]; jobs: Job[]; validity: Record<string, Validity> }
export type AgentRunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'budget_exhausted';
export interface AgentRunInput {
  question: string;
  kind: 'question' | 'claim' | 'hypothesis';
  mode: 'guarded' | 'baseline';
  completion_criteria: string[];
  falsifiers: string[];
  max_turns?: number;
  max_web_searches?: number;
}
export interface AgentRun {
  id: string; workspace_id: string; goal_id: string; mode: string; model: string;
  question: string; kind: AgentRunInput['kind'];
  status: AgentRunStatus;
  budgets: { max_turns: number; max_web_searches: number; max_total_output_tokens: number };
  usage: { turns?: number; web_searches?: number; input_tokens?: number; output_tokens?: number; cost_usd?: number; judge?: Record<string, unknown> };
  error: string | null; final_report: string | null; final_statement_id: string | null;
  certainty: 'established' | 'conditional' | 'hypothesis' | 'abstained' | null;
  created_at: string; started_at: string | null; completed_at: string | null;
}
export interface AgentEvent { seq: number; type: string; payload: Record<string, unknown>; created_at: string }
