import { describe, it, expect } from 'vitest';
import { projectWorkspace, filterGraph, allObligations } from '../src/lib/research/graph';
import { stableLayout } from '../src/lib/research/layout';
import type { Snapshot } from '../src/types/api';
const state = {
  workspace: { id: 'w', title: 'Study', created_at: '2026-10-03' },
  graph: {
    statements: [{ id: 'premise', text: 'Evidence', assertion_mode: 'reported', role: 'premise', salience: 'supporting', lifecycle: 'accepted', excerpt_ids: ['excerpt'], provenance: { actor_type: 'user', actor_id: 'reviewer' } }, { id: 'conclusion', text: 'Human outcome', assertion_mode: 'asserted', role: 'conclusion', salience: 'core', lifecycle: 'proposed', excerpt_ids: [], provenance: { actor_type: 'extractor', actor_id: 'anthropic' } }],
    reasoning_steps: [{ id: 'step', explanation: 'Inference', premise_ids: ['premise'], conclusion_id: 'conclusion', lifecycle: 'proposed' }],
    relations: [{ id: 'relation', source_node_id: 'premise', target_node_id: 'conclusion', relation: 'qualifies', metadata: { lifecycle: 'proposed', audit_verdict: 'needs_review' } }],
  },
  sources: [{ id: 'source', original_filename: 'study.md', excerpts: [{ id: 'excerpt', source_id: 'source', text: 'Exact evidence quote.', sequence: 0, locator: { start: 0, end: 21 } }] }],
  contexts: [{ focus_statement: { id: 'conclusion' }, obligations: [{ id: 'obligation', blocks_statement_id: 'conclusion', blocks_node_id: 'conclusion', description: 'Ground the conclusion', kind: 'evidence_or_inference', status: 'open' }], issues: [] }], jobs: [], validity: {},
} as unknown as Snapshot;
describe('backend graph projection', () => {
  it('preserves excerpt provenance, premise groups, generated lifecycle and obligations', () => {
    const graph = projectWorkspace(state, true);
    expect(graph.nodes.find(n => n.id === 'excerpt')).toMatchObject({ label: 'Exact evidence quote.', sourceIds: ['source'] });
    expect(graph.nodes.find(n => n.id === 'premise')).toMatchObject({ state: 'idle', proposed: false });
    expect(graph.nodes.find(n => n.id === 'conclusion')).toMatchObject({ kind: 'conclusion', state: 'fail', proposed: true });
    expect(graph.edges).toContainEqual(expect.objectContaining({ source: 'premise', target: 'step', relation: 'premise_of' }));
    expect(graph.edges).toContainEqual(expect.objectContaining({ source: 'step', target: 'conclusion', relation: 'concludes' }));
    expect(graph.edges).toContainEqual(expect.objectContaining({ id: 'relation', proposed: true, auditVerdict: 'needs_review' }));
  });
  it('keeps unknown linked excerpts visible without inventing quote text', () => {
    const graph = projectWorkspace({ ...state, sources: [] }, true);
    expect(graph.nodes.find(n => n.id === 'excerpt')).toMatchObject({ label: 'Linked excerpt', state: 'unknown' });
    expect(graph.edges.some(e => e.source === 'excerpt' && e.target === 'premise')).toBe(true);
  });
  it('shows source invalidation and deduplicates context obligations', () => {
    const invalidated = { ...state, validity: { source: { status: 'invalidated' } }, contexts: [...state.contexts, ...state.contexts] } as unknown as Snapshot;
    expect(projectWorkspace(invalidated, true).nodes.find(n => n.id === 'excerpt')?.state).toBe('fail');
    expect(allObligations(invalidated)).toHaveLength(1);
  });
  it('filters locally and retains one-hop dependency context', () => {
    const graph = projectWorkspace(state, true);
    expect(filterGraph(graph, 'Exact evidence', '', '').nodes.map(n => n.id)).toEqual(['excerpt', 'premise']);
    expect(filterGraph(graph, 'nonexistent', '', '').nodes).toEqual([]);
    const positions = stableLayout(graph);
    const next = stableLayout({ ...graph, nodes: [...graph.nodes, { id: 'new', kind: 'statement', label: 'New', detail: 'proposed', sourceIds: [], proposed: true, state: 'warn' }] }, positions);
    expect(next.excerpt).toEqual(positions.excerpt);
    expect(next.premise).toEqual(positions.premise);
  });
  it('defaults to core claims and their required reasoning chain without excerpt nodes', () => {
    const graph = projectWorkspace(state);
    expect(graph.nodes.map(n => n.id)).toEqual(expect.arrayContaining(['premise', 'step', 'conclusion']));
    expect(graph.nodes.some(n => n.kind === 'excerpt')).toBe(false);
  });
  it('keeps secondary claims in the detailed evidence view', () => {
    const secondary = { id: 'secondary', text: 'Subgroup result', assertion_mode: 'reported', role: 'conclusion', salience: 'secondary', lifecycle: 'proposed', excerpt_ids: [], provenance: { actor_type: 'extractor', actor_id: 'anthropic' } };
    const withSecondary = { ...state, graph: { ...state.graph, statements: [...state.graph.statements, secondary] } } as unknown as Snapshot;

    expect(projectWorkspace(withSecondary).nodes.some(node => node.id === 'secondary')).toBe(false);
    expect(projectWorkspace(withSecondary, true).nodes.find(node => node.id === 'secondary')?.detail).toContain('secondary');
  });
});
