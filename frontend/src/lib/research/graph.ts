import type { AgentEvent, AgentRun, Obligation, Snapshot } from '../../types/api';
export type ResearchKind = 'statement' | 'conclusion' | 'step' | 'excerpt' | 'obligation' | 'goal';
export type ResearchState = 'idle' | 'unknown' | 'pass' | 'warn' | 'fail';
export interface ResearchNode { id: string; kind: ResearchKind; label: string; detail: string; state: ResearchState; proposed: boolean; sourceIds: string[] }
export interface ResearchEdge { id: string; source: string; target: string; relation: string; proposed?: boolean; auditVerdict?: string }
export interface ResearchGraph { nodes: ResearchNode[]; edges: ResearchEdge[] }
export const humanize = (value: string) => value.replace(/_/g, ' ');
export function allObligations(state: Snapshot): Obligation[] { return [...new Map(state.contexts.flatMap(c => c.obligations).map(o => [o.id, o])).values()]; }
export function projectWorkspace(state: Snapshot, detailed = false, include: ReadonlySet<string> = new Set()): ResearchGraph {
  const nodes: ResearchNode[] = []; const edges: ResearchEdge[] = [];
  const obligations = allObligations(state).filter(o => o.status === 'open');
  const excerpts = new Map(state.sources.flatMap(s => s.excerpts).map(e => [e.id, e]));
  // `include` forces highlighted claims into the overview even when they are not core.
  const visibleStatements = new Set(detailed ? state.graph.statements.map(s => s.id) : state.graph.statements.filter(s => s.salience === 'core' || include.has(s.id)).map(s => s.id));
  const visibleSteps = new Set<string>();
  if (!detailed) {
    let changed = true;
    while (changed) {
      changed = false;
      for (const step of state.graph.reasoning_steps) {
        if (visibleStatements.has(step.conclusion_id) && !visibleSteps.has(step.id)) {
          visibleSteps.add(step.id); changed = true;
          for (const premise of step.premise_ids) if (!visibleStatements.has(premise)) { visibleStatements.add(premise); changed = true; }
        }
      }
    }
  } else for (const step of state.graph.reasoning_steps) visibleSteps.add(step.id);
  if (detailed) for (const e of excerpts.values()) {
    nodes.push({ id: e.id, kind: 'excerpt', label: e.text, detail: state.sources.find(s => s.id === e.source_id)?.original_filename ?? 'Source excerpt', state: state.validity[e.source_id]?.status === 'invalidated' ? 'fail' : 'idle', proposed: false, sourceIds: [e.source_id] });
  }
  const sourceIds = (ids: string[]) => [...new Set(ids.flatMap(id => { const e = excerpts.get(id); return e ? [e.source_id] : []; }))];
  for (const s of state.graph.statements.filter(item => visibleStatements.has(item.id))) {
    // Review acceptance is a lifecycle decision, never a scientific truth score.
    const status: ResearchState = s.lifecycle === 'rejected' ? 'unknown' : obligations.some(o => o.blocks_statement_id === s.id) ? 'fail' : s.lifecycle === 'proposed' ? 'warn' : 'idle';
    nodes.push({ id: s.id, kind: s.role === 'conclusion' ? 'conclusion' : 'statement', label: s.text, detail: `${s.lifecycle} · ${s.salience} · ${humanize(s.assertion_mode)}${s.role ? ` · ${s.role}` : ''} · ${s.provenance.model ?? s.provenance.actor_id}`, state: status, proposed: s.lifecycle === 'proposed', sourceIds: sourceIds(s.excerpt_ids) });
    if (detailed) for (const id of s.excerpt_ids) {
      if (!excerpts.has(id) && !nodes.some(n => n.id === id)) nodes.push({ id, kind: 'excerpt', label: 'Linked excerpt', detail: 'Attach the source in Material to load its exact text', state: 'unknown', proposed: false, sourceIds: [] });
      edges.push({ id: `grounds:${id}:${s.id}`, source: id, target: s.id, relation: 'grounds' });
    }
  }
  for (const r of state.graph.reasoning_steps.filter(item => visibleSteps.has(item.id))) {
    nodes.push({ id: r.id, kind: 'step', label: r.explanation, detail: `${r.lifecycle} · ${r.premise_ids.length} required premises · ${r.provenance?.model ?? r.provenance?.actor_id ?? ""}`, state: r.lifecycle === 'proposed' ? 'warn' : r.lifecycle === 'rejected' ? 'unknown' : 'idle', proposed: r.lifecycle === 'proposed', sourceIds: sourceIds(state.graph.statements.filter(s => r.premise_ids.includes(s.id)).flatMap(s => s.excerpt_ids)) });
    for (const p of r.premise_ids) edges.push({ id: `premise:${p}:${r.id}`, source: p, target: r.id, relation: 'premise_of', proposed: r.lifecycle === 'proposed' });
    edges.push({ id: `conclusion:${r.id}`, source: r.id, target: r.conclusion_id, relation: 'concludes', proposed: r.lifecycle === 'proposed' });
  }
  for (const o of obligations) {
    nodes.push({ id: o.id, kind: 'obligation', label: o.description, detail: humanize(o.kind), state: 'fail', proposed: false, sourceIds: [] });
    const target = o.blocks_node_id ?? o.blocks_statement_id;
    if (target) edges.push({ id: `blocks:${o.id}`, source: o.id, target, relation: 'blocks' });
  }
  for (const e of state.graph.relations) edges.push({ id: e.id, source: e.source_node_id, target: e.target_node_id, relation: e.relation, proposed: e.metadata.lifecycle === 'proposed' || !!e.metadata.audit_verdict, auditVerdict: typeof e.metadata.audit_verdict === 'string' ? e.metadata.audit_verdict : undefined });
  const ids = new Set(nodes.map(n => n.id));
  return { nodes, edges: edges.filter(e => ids.has(e.source) && ids.has(e.target)) };
}
export function filterGraph(graph: ResearchGraph, query: string, lifecycle: string, source: string): ResearchGraph {
  const match = graph.nodes.filter(n => (!query || `${n.label} ${n.id}`.toLowerCase().includes(query.toLowerCase())) && (!lifecycle || n.detail.startsWith(lifecycle)) && (!source || n.sourceIds.includes(source)));
  if (!query && !lifecycle && !source) return graph;
  const ids = new Set(match.map(n => n.id));
  const adjacent = new Set(ids);
  for (const e of graph.edges) { if (ids.has(e.source)) adjacent.add(e.target); if (ids.has(e.target)) adjacent.add(e.source); }
  return { nodes: graph.nodes.filter(n => adjacent.has(n.id)), edges: graph.edges.filter(e => adjacent.has(e.source) && adjacent.has(e.target)) };
}

/** The part of the logical chain that links the given claims: the claims, the given steps, and
 *  every step whose conclusion and at least one premise are both among the claims. */
export function chainHighlight(state: Snapshot, statementIds: string[], stepIds: string[] = []): string[] {
  const claims = new Set(statementIds);
  const steps = new Set(stepIds);
  for (const step of state.graph.reasoning_steps) {
    if (steps.has(step.id)) { claims.add(step.conclusion_id); continue; }
    if (claims.has(step.conclusion_id) && step.premise_ids.some(id => claims.has(id))) steps.add(step.id);
  }
  return [...claims, ...steps];
}
/** What a claim rests on: its derivation, recursively. */
export function supportChain(state: Snapshot, statementId: string, depth = 6): string[] {
  const claims = new Set([statementId]); const steps = new Set<string>();
  let frontier = [statementId];
  for (let level = 0; level < depth && frontier.length; level++) {
    const next: string[] = [];
    for (const step of state.graph.reasoning_steps) if (frontier.includes(step.conclusion_id) && !steps.has(step.id)) {
      steps.add(step.id);
      for (const premise of step.premise_ids) if (!claims.has(premise)) { claims.add(premise); next.push(premise); }
    }
    frontier = next;
  }
  return [...claims, ...steps];
}
export const GRAPH_TOOLS = ['graph_overview', 'search_graph', 'get_graph_node', 'trace_chain'];
/** The nodes an agent run's answer rests on: its final conclusion's support chain, plus the
 *  graph nodes it inspected and linked. Only ids present in the graph are returned. */
export function runHighlight(state: Snapshot | undefined, run: AgentRun, events: AgentEvent[]): string[] {
  if (!state) return [];
  const known = new Set([...state.graph.statements.map(s => s.id), ...state.graph.reasoning_steps.map(s => s.id)]);
  const ids = new Set<string>(run.final_statement_id ? supportChain(state, run.final_statement_id) : []);
  for (const event of events) {
    if (event.type !== 'tool_call') continue;
    const name = String(event.payload.name ?? ''); const input = (event.payload.input ?? {}) as Record<string, unknown>;
    if (GRAPH_TOOLS.includes(name) || name === 'link_claims') for (const key of ['node_id', 'statement_id', 'source_statement_id', 'target_statement_id']) if (typeof input[key] === 'string') ids.add(input[key] as string);
  }
  return chainHighlight(state, [...ids].filter(id => known.has(id) && state.graph.statements.some(s => s.id === id)), [...ids].filter(id => known.has(id) && state.graph.reasoning_steps.some(s => s.id === id)));
}
