import type {
  AgentEvent,
  AgentRun,
  Obligation,
  ReasoningStep,
  Snapshot,
  Source,
  Statement,
} from '../../types/api';
/** What to call a source on screen: its title (a paper's name), else the uploaded file's name. */
export const sourceName = (source: Pick<Source, 'title' | 'original_filename'>) =>
  source.title?.trim() || source.original_filename;
export type ResearchKind = 'statement' | 'conclusion' | 'step' | 'excerpt' | 'obligation' | 'goal';
export type ResearchState = 'idle' | 'unknown' | 'pass' | 'warn' | 'fail';
export interface ResearchNode {
  id: string;
  kind: ResearchKind;
  label: string;
  detail: string;
  lifecycle?: string; // review state of a claim or step, for the review filter
  state: ResearchState;
  proposed: boolean;
  sourceIds: string[];
}
export interface ResearchEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  proposed?: boolean;
  auditVerdict?: string;
}
export interface ResearchGraph {
  nodes: ResearchNode[];
  edges: ResearchEdge[];
}
export const humanize = (value: string) => value.replace(/_/g, ' ');
const capital = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);
/** Who put a claim or step in the graph, in words a reader understands. */
function origin(provenance?: { actor_type: string }) {
  return (
    {
      agent: 'Research agent',
      extractor: 'From the paper',
      user: 'Added by you',
      integration: 'Imported',
      rule_engine: 'Verifier',
    }[provenance?.actor_type ?? ''] ?? ''
  );
}
/** One short line under a node: review state (unless accepted), what it is, where it came from. */
const nodeDetail = (...parts: (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(' · ');
const reviewState = (lifecycle: string) =>
  lifecycle === 'proposed' ? 'Needs review' : lifecycle === 'rejected' ? 'Rejected' : undefined;
/** The one-line summary of a claim, shown under graph nodes and in the inspector. */
export const describeStatement = (s: Statement) =>
  nodeDetail(
    reviewState(s.lifecycle),
    s.role && capital(s.role),
    s.assertion_mode === 'hypothesis' && 'Hypothesis',
    s.salience === 'secondary' && 'Secondary',
    origin(s.provenance),
  );
/** The one-line summary of a reasoning step. */
export const describeStep = (r: ReasoningStep) =>
  nodeDetail(
    reviewState(r.lifecycle),
    `${r.premise_ids.length} ${r.premise_ids.length === 1 ? 'premise' : 'premises, all required'}`,
    origin(r.provenance),
  );
/** Verifier text for people: it sometimes names objects by internal id ("Statement 971af531
 *  cites..."), which means nothing to a reader. */
export const readableFinding = (text: string) =>
  text.replace(/\b(statement|step|claim)\s+[0-9a-f]{8}(-[0-9a-f-]{27})?\b/gi, 'a claim');
export function allObligations(state: Snapshot): Obligation[] {
  return [...new Map(state.contexts.flatMap(c => c.obligations).map(o => [o.id, o])).values()];
}
export function projectWorkspace(
  state: Snapshot,
  detailed = false,
  include: ReadonlySet<string> = new Set(),
): ResearchGraph {
  const nodes: ResearchNode[] = [];
  const edges: ResearchEdge[] = [];
  const obligations = allObligations(state).filter(o => o.status === 'open');
  const excerpts = new Map(state.sources.flatMap(s => s.excerpts).map(e => [e.id, e]));
  // `include` forces highlighted claims into the overview even when they are not core.
  const visibleStatements = new Set(
    detailed
      ? state.graph.statements.map(s => s.id)
      : state.graph.statements
          .filter(s => s.salience === 'core' || include.has(s.id))
          .map(s => s.id),
  );
  const visibleSteps = new Set<string>();
  if (!detailed) {
    let changed = true;
    while (changed) {
      changed = false;
      // Keep the evidence that supports, challenges or qualifies a visible claim in view.
      for (const relation of state.graph.relations) {
        if (!['supports', 'rebuts', 'qualifies'].includes(relation.relation)) continue;
        const { source_node_id: source, target_node_id: target } = relation;
        if (visibleStatements.has(source) || visibleStatements.has(target)) {
          for (const id of [source, target])
            if (!visibleStatements.has(id) && state.graph.statements.some(s => s.id === id)) {
              visibleStatements.add(id);
              changed = true;
            }
        }
      }
      for (const step of state.graph.reasoning_steps) {
        if (visibleStatements.has(step.conclusion_id) && !visibleSteps.has(step.id)) {
          visibleSteps.add(step.id);
          changed = true;
          for (const premise of step.premise_ids)
            if (!visibleStatements.has(premise)) {
              visibleStatements.add(premise);
              changed = true;
            }
        }
      }
    }
  } else for (const step of state.graph.reasoning_steps) visibleSteps.add(step.id);
  if (detailed)
    for (const e of excerpts.values()) {
      nodes.push({
        id: e.id,
        kind: 'excerpt',
        label: e.text,
        detail: (() => {
          const source = state.sources.find(s => s.id === e.source_id);
          return source ? sourceName(source) : 'Source excerpt';
        })(),
        state: state.validity[e.source_id]?.status === 'invalidated' ? 'fail' : 'idle',
        proposed: false,
        sourceIds: [e.source_id],
      });
    }
  const sourceIds = (ids: string[]) => [
    ...new Set(
      ids.flatMap(id => {
        const e = excerpts.get(id);
        return e ? [e.source_id] : [];
      }),
    ),
  ];
  for (const s of state.graph.statements.filter(item => visibleStatements.has(item.id))) {
    // Review acceptance is a lifecycle decision, never a scientific truth score.
    const status: ResearchState =
      s.lifecycle === 'rejected'
        ? 'unknown'
        : obligations.some(o => o.blocks_statement_id === s.id)
          ? 'fail'
          : s.lifecycle === 'proposed'
            ? 'warn'
            : 'idle';
    nodes.push({
      id: s.id,
      kind: s.role === 'conclusion' ? 'conclusion' : 'statement',
      label: s.text,
      detail: describeStatement(s),
      lifecycle: s.lifecycle,
      state: status,
      proposed: s.lifecycle === 'proposed',
      sourceIds: sourceIds(s.excerpt_ids),
    });
    if (detailed)
      for (const id of s.excerpt_ids) {
        if (!excerpts.has(id) && !nodes.some(n => n.id === id))
          nodes.push({
            id,
            kind: 'excerpt',
            label: 'Linked excerpt',
            detail: 'Attach the source in Material to load its exact text',
            state: 'unknown',
            proposed: false,
            sourceIds: [],
          });
        edges.push({ id: `grounds:${id}:${s.id}`, source: id, target: s.id, relation: 'grounds' });
      }
  }
  for (const r of state.graph.reasoning_steps.filter(item => visibleSteps.has(item.id))) {
    nodes.push({
      id: r.id,
      kind: 'step',
      label: r.explanation,
      detail: describeStep(r),
      lifecycle: r.lifecycle,
      state: r.lifecycle === 'proposed' ? 'warn' : r.lifecycle === 'rejected' ? 'unknown' : 'idle',
      proposed: r.lifecycle === 'proposed',
      sourceIds: sourceIds(
        state.graph.statements
          .filter(s => r.premise_ids.includes(s.id))
          .flatMap(s => s.excerpt_ids),
      ),
    });
    for (const p of r.premise_ids)
      edges.push({
        id: `premise:${p}:${r.id}`,
        source: p,
        target: r.id,
        relation: 'premise_of',
        proposed: r.lifecycle === 'proposed',
      });
    edges.push({
      id: `conclusion:${r.id}`,
      source: r.id,
      target: r.conclusion_id,
      relation: 'concludes',
      proposed: r.lifecycle === 'proposed',
    });
  }
  for (const o of obligations) {
    nodes.push({
      id: o.id,
      kind: 'obligation',
      label: o.description,
      detail: capital(humanize(o.kind)),
      state: 'fail',
      proposed: false,
      sourceIds: [],
    });
    const target = o.blocks_node_id ?? o.blocks_statement_id;
    if (target) edges.push({ id: `blocks:${o.id}`, source: o.id, target, relation: 'blocks' });
  }
  for (const e of state.graph.relations)
    edges.push({
      id: e.id,
      source: e.source_node_id,
      target: e.target_node_id,
      relation: e.relation,
      proposed: e.metadata.lifecycle === 'proposed' || !!e.metadata.audit_verdict,
      auditVerdict:
        typeof e.metadata.audit_verdict === 'string' ? e.metadata.audit_verdict : undefined,
    });
  const ids = new Set(nodes.map(n => n.id));
  return { nodes, edges: edges.filter(e => ids.has(e.source) && ids.has(e.target)) };
}
/** Links between claims that are not the argument's own structure: evidence the agent or synthesis
 *  connected across sources. They are many in a researched workspace, so they are laid out around
 *  the argument rather than shaping it, and drawn when one of their claims is in focus. */
export const CROSS_LINKS = new Set([
  'supports',
  'rebuts',
  'qualifies',
  'undercuts',
  'specializes',
  'revises',
]);

/** Claims and steps that were withdrawn (rejected) or replaced by a revision. */
export function withdrawnIds(graph: ResearchGraph): Set<string> {
  const replaced = graph.edges.filter(e => e.relation === 'revises').map(e => e.target);
  return new Set([
    ...graph.nodes.filter(n => n.lifecycle === 'rejected').map(n => n.id),
    ...replaced,
  ]);
}

/** The graph without withdrawn or replaced claims and steps: a revision stands in for what it
 *  replaced. */
export function withoutWithdrawn(graph: ResearchGraph): ResearchGraph {
  const gone = withdrawnIds(graph);
  const kept = graph.nodes.filter(n => !gone.has(n.id));
  const ids = new Set(kept.map(n => n.id));
  return { nodes: kept, edges: graph.edges.filter(e => ids.has(e.source) && ids.has(e.target)) };
}

export function filterGraph(
  graph: ResearchGraph,
  query: string,
  lifecycle: string,
  source: string,
): ResearchGraph {
  const match = graph.nodes.filter(
    n =>
      (!query || `${n.label} ${n.id}`.toLowerCase().includes(query.toLowerCase())) &&
      (!lifecycle || n.lifecycle === lifecycle) &&
      (!source || n.sourceIds.includes(source)),
  );
  if (!query && !lifecycle && !source) return graph;
  const ids = new Set(match.map(n => n.id));
  const adjacent = new Set(ids);
  for (const e of graph.edges) {
    if (ids.has(e.source)) adjacent.add(e.target);
    if (ids.has(e.target)) adjacent.add(e.source);
  }
  return {
    nodes: graph.nodes.filter(n => adjacent.has(n.id)),
    edges: graph.edges.filter(e => adjacent.has(e.source) && adjacent.has(e.target)),
  };
}

/** The part of the logical chain that links the given claims: the claims, the given steps, and
 *  every step whose conclusion and at least one premise are both among the claims. */
export function chainHighlight(
  state: Snapshot,
  statementIds: string[],
  stepIds: string[] = [],
): string[] {
  const claims = new Set(statementIds);
  const steps = new Set(stepIds);
  for (const step of state.graph.reasoning_steps) {
    if (steps.has(step.id)) {
      claims.add(step.conclusion_id);
      continue;
    }
    if (claims.has(step.conclusion_id) && step.premise_ids.some(id => claims.has(id)))
      steps.add(step.id);
  }
  return [...claims, ...steps];
}
/** What a claim rests on: its derivation, recursively. */
export function supportChain(state: Snapshot, statementId: string, depth = 6): string[] {
  const claims = new Set([statementId]);
  const steps = new Set<string>();
  let frontier = [statementId];
  for (let level = 0; level < depth && frontier.length; level++) {
    const next: string[] = [];
    for (const step of state.graph.reasoning_steps)
      if (frontier.includes(step.conclusion_id) && !steps.has(step.id)) {
        steps.add(step.id);
        for (const premise of step.premise_ids)
          if (!claims.has(premise)) {
            claims.add(premise);
            next.push(premise);
          }
      }
    frontier = next;
  }
  return [...claims, ...steps];
}
export const GRAPH_TOOLS = ['graph_overview', 'search_graph', 'get_graph_node', 'trace_chain'];
/** The nodes an agent run's answer rests on: its final conclusion's support chain, plus the
 *  graph nodes it inspected and linked. Only ids present in the graph are returned. */
export function runHighlight(
  state: Snapshot | undefined,
  run: AgentRun,
  events: AgentEvent[],
): string[] {
  if (!state) return [];
  const known = new Set([
    ...state.graph.statements.map(s => s.id),
    ...state.graph.reasoning_steps.map(s => s.id),
  ]);
  const ids = new Set<string>(
    run.final_statement_id ? supportChain(state, run.final_statement_id) : [],
  );
  for (const event of events) {
    if (event.type !== 'tool_call') continue;
    const name = String(event.payload.name ?? '');
    const input = (event.payload.input ?? {}) as Record<string, unknown>;
    if (GRAPH_TOOLS.includes(name) || name === 'link_claims')
      for (const key of ['node_id', 'statement_id', 'source_statement_id', 'target_statement_id'])
        if (typeof input[key] === 'string') ids.add(input[key] as string);
  }
  return chainHighlight(
    state,
    [...ids].filter(id => known.has(id) && state.graph.statements.some(s => s.id === id)),
    [...ids].filter(id => known.has(id) && state.graph.reasoning_steps.some(s => s.id === id)),
  );
}
