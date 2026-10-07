import { describe, it, expect } from 'vitest';
import {
  projectWorkspace,
  filterGraph,
  allObligations,
  sourceName,
} from '../src/lib/research/graph';
import { stableLayout } from '../src/lib/research/layout';
import type { Snapshot } from '../src/types/api';
const state = {
  workspace: { id: 'w', title: 'Study', created_at: '2026-10-03' },
  graph: {
    statements: [
      {
        id: 'premise',
        text: 'Evidence',
        assertion_mode: 'reported',
        role: 'premise',
        salience: 'supporting',
        lifecycle: 'accepted',
        excerpt_ids: ['excerpt'],
        provenance: { actor_type: 'user', actor_id: 'reviewer' },
      },
      {
        id: 'conclusion',
        text: 'Human outcome',
        assertion_mode: 'asserted',
        role: 'conclusion',
        salience: 'core',
        lifecycle: 'proposed',
        excerpt_ids: [],
        provenance: { actor_type: 'extractor', actor_id: 'anthropic' },
      },
    ],
    reasoning_steps: [
      {
        id: 'step',
        explanation: 'Inference',
        premise_ids: ['premise'],
        conclusion_id: 'conclusion',
        lifecycle: 'proposed',
      },
    ],
    relations: [
      {
        id: 'relation',
        source_node_id: 'premise',
        target_node_id: 'conclusion',
        relation: 'qualifies',
        metadata: { lifecycle: 'proposed', audit_verdict: 'needs_review' },
      },
    ],
  },
  sources: [
    {
      id: 'source',
      original_filename: 'study.md',
      excerpts: [
        {
          id: 'excerpt',
          source_id: 'source',
          text: 'Exact evidence quote.',
          sequence: 0,
          locator: { start: 0, end: 21 },
        },
      ],
    },
  ],
  contexts: [
    {
      focus_statement: { id: 'conclusion' },
      obligations: [
        {
          id: 'obligation',
          blocks_statement_id: 'conclusion',
          blocks_node_id: 'conclusion',
          description: 'Ground the conclusion',
          kind: 'evidence_or_inference',
          status: 'open',
        },
      ],
      issues: [],
    },
  ],
  jobs: [],
  validity: {},
} as unknown as Snapshot;
describe('backend graph projection', () => {
  it('preserves excerpt provenance, premise groups, generated lifecycle and obligations', () => {
    const graph = projectWorkspace(state, true);
    expect(graph.nodes.find(n => n.id === 'excerpt')).toMatchObject({
      label: 'Exact evidence quote.',
      sourceIds: ['source'],
    });
    expect(graph.nodes.find(n => n.id === 'premise')).toMatchObject({
      state: 'idle',
      proposed: false,
    });
    expect(graph.nodes.find(n => n.id === 'conclusion')).toMatchObject({
      kind: 'conclusion',
      state: 'fail',
      proposed: true,
    });
    expect(graph.edges).toContainEqual(
      expect.objectContaining({ source: 'premise', target: 'step', relation: 'premise_of' }),
    );
    expect(graph.edges).toContainEqual(
      expect.objectContaining({ source: 'step', target: 'conclusion', relation: 'concludes' }),
    );
    expect(graph.edges).toContainEqual(
      expect.objectContaining({ id: 'relation', proposed: true, auditVerdict: 'needs_review' }),
    );
  });
  it('keeps unknown linked excerpts visible without inventing quote text', () => {
    const graph = projectWorkspace({ ...state, sources: [] }, true);
    expect(graph.nodes.find(n => n.id === 'excerpt')).toMatchObject({
      label: 'Linked excerpt',
      state: 'unknown',
    });
    expect(graph.edges.some(e => e.source === 'excerpt' && e.target === 'premise')).toBe(true);
  });
  it('shows source invalidation and deduplicates context obligations', () => {
    const invalidated = {
      ...state,
      validity: { source: { status: 'invalidated' } },
      contexts: [...state.contexts, ...state.contexts],
    } as unknown as Snapshot;
    expect(projectWorkspace(invalidated, true).nodes.find(n => n.id === 'excerpt')?.state).toBe(
      'fail',
    );
    expect(allObligations(invalidated)).toHaveLength(1);
  });
  it('filters locally and retains one-hop dependency context', () => {
    const graph = projectWorkspace(state, true);
    expect(filterGraph(graph, 'Exact evidence', '', '').nodes.map(n => n.id)).toEqual([
      'excerpt',
      'premise',
    ]);
    expect(filterGraph(graph, 'nonexistent', '', '').nodes).toEqual([]);
    const positions = stableLayout(graph);
    const next = stableLayout(
      {
        ...graph,
        nodes: [
          ...graph.nodes,
          {
            id: 'new',
            kind: 'statement',
            label: 'New',
            detail: 'proposed',
            sourceIds: [],
            proposed: true,
            state: 'warn',
          },
        ],
      },
      positions,
    );
    expect(next.excerpt).toEqual(positions.excerpt);
    expect(next.premise).toEqual(positions.premise);
  });
  it('defaults to core claims and their required reasoning chain without excerpt nodes', () => {
    const graph = projectWorkspace(state);
    expect(graph.nodes.map(n => n.id)).toEqual(
      expect.arrayContaining(['premise', 'step', 'conclusion']),
    );
    expect(graph.nodes.some(n => n.kind === 'excerpt')).toBe(false);
  });
  it('shows supporting and opposing evidence linked to core claims in the overview', () => {
    const evidence = { ...state.graph.statements[0], id: 'opposing', excerpt_ids: [] };
    const withLink = {
      ...state,
      graph: {
        ...state.graph,
        statements: [...state.graph.statements, evidence],
        relations: [
          ...state.graph.relations,
          {
            id: 'rebuttal',
            source_node_id: 'opposing',
            target_node_id: 'conclusion',
            relation: 'rebuts',
            metadata: {},
          },
        ],
      },
    } as Snapshot;
    const graph = projectWorkspace(withLink);
    expect(graph.nodes.some(n => n.id === 'opposing')).toBe(true);
    expect(graph.edges.some(e => e.id === 'rebuttal')).toBe(true);
  });
  it('keeps secondary claims in the detailed evidence view', () => {
    const secondary = {
      id: 'secondary',
      text: 'Subgroup result',
      assertion_mode: 'reported',
      role: 'conclusion',
      salience: 'secondary',
      lifecycle: 'proposed',
      excerpt_ids: [],
      provenance: { actor_type: 'extractor', actor_id: 'anthropic' },
    };
    const withSecondary = {
      ...state,
      graph: { ...state.graph, statements: [...state.graph.statements, secondary] },
    } as unknown as Snapshot;

    expect(projectWorkspace(withSecondary).nodes.some(node => node.id === 'secondary')).toBe(false);
    expect(
      projectWorkspace(withSecondary, true).nodes.find(node => node.id === 'secondary')?.detail,
    ).toBe('Needs review · Conclusion · Secondary · From the paper');
  });
});

import { chainHighlight, runHighlight, supportChain } from '../src/lib/research/graph';
import type { AgentEvent, AgentRun } from '../src/types/api';
describe('reasoning highlights', () => {
  const chain = {
    ...state,
    graph: {
      statements: [
        {
          id: 'a',
          text: 'Trial result',
          salience: 'supporting',
          lifecycle: 'accepted',
          assertion_mode: 'reported',
          role: null,
          excerpt_ids: [],
          provenance: { actor_type: 'user', actor_id: 'r' },
        },
        {
          id: 'b',
          text: 'Mechanism',
          salience: 'supporting',
          lifecycle: 'accepted',
          assertion_mode: 'reported',
          role: null,
          excerpt_ids: [],
          provenance: { actor_type: 'user', actor_id: 'r' },
        },
        {
          id: 'c',
          text: 'Intermediate',
          salience: 'core',
          lifecycle: 'accepted',
          assertion_mode: 'asserted',
          role: null,
          excerpt_ids: [],
          provenance: { actor_type: 'user', actor_id: 'r' },
        },
        {
          id: 'd',
          text: 'Conclusion',
          salience: 'core',
          lifecycle: 'accepted',
          assertion_mode: 'asserted',
          role: 'conclusion',
          excerpt_ids: [],
          provenance: { actor_type: 'user', actor_id: 'r' },
        },
        {
          id: 'z',
          text: 'Unrelated',
          salience: 'core',
          lifecycle: 'accepted',
          assertion_mode: 'asserted',
          role: null,
          excerpt_ids: [],
          provenance: { actor_type: 'user', actor_id: 'r' },
        },
      ],
      reasoning_steps: [
        {
          id: 's1',
          explanation: 'a and b give c',
          premise_ids: ['a', 'b'],
          conclusion_id: 'c',
          lifecycle: 'accepted',
        },
        {
          id: 's2',
          explanation: 'c gives d',
          premise_ids: ['c'],
          conclusion_id: 'd',
          lifecycle: 'accepted',
        },
      ],
      relations: [],
    },
    contexts: [],
  } as unknown as Snapshot;

  it('connects highlighted claims through the steps between them, and nothing else', () => {
    expect(new Set(chainHighlight(chain, ['a', 'c']))).toEqual(new Set(['a', 'c', 's1']));
    expect(new Set(chainHighlight(chain, [], ['s2']))).toEqual(new Set(['s2', 'd']));
  });

  it('traces what a conclusion rests on, recursively', () => {
    expect(new Set(supportChain(chain, 'd'))).toEqual(new Set(['d', 'c', 'a', 'b', 's1', 's2']));
  });

  it('highlights an agent run’s conclusion chain and the nodes it queried', () => {
    const run = { final_statement_id: 'c' } as AgentRun;
    const events = [
      {
        seq: 1,
        type: 'tool_call',
        created_at: '',
        payload: { name: 'get_graph_node', input: { node_id: 'z' } },
      },
      {
        seq: 2,
        type: 'tool_call',
        created_at: '',
        payload: { name: 'get_graph_node', input: { node_id: 'not-in-graph' } },
      },
    ] as AgentEvent[];
    expect(new Set(runHighlight(chain, run, events))).toEqual(new Set(['c', 'a', 'b', 's1', 'z']));
  });

  it('brings highlighted supporting claims into the overview', () => {
    const loose = { ...chain, graph: { ...chain.graph, reasoning_steps: [] } } as Snapshot;
    expect(projectWorkspace(loose).nodes.some(node => node.id === 'a')).toBe(false);
    expect(projectWorkspace(loose, false, new Set(['a'])).nodes.some(node => node.id === 'a')).toBe(
      true,
    );
  });
});

describe('sourceName', () => {
  it('prefers the title and falls back to the file name', () => {
    expect(
      sourceName({ title: 'Dexamethasone in Covid-19', original_filename: 'AMBC_x.json' }),
    ).toBe('Dexamethasone in Covid-19');
    expect(sourceName({ title: '  ', original_filename: 'notes.md' })).toBe('notes.md');
    expect(sourceName({ title: null, original_filename: 'notes.md' })).toBe('notes.md');
  });
});

describe('node details', () => {
  it('filters by review state without parsing the detail text', () => {
    const graph = projectWorkspace(state, true);
    const accepted = graph.nodes.filter(n => n.lifecycle === 'accepted').map(n => n.id);
    expect(accepted.length).toBeGreaterThan(0);
    const ids = filterGraph(graph, '', 'accepted', '').nodes.map(n => n.id);
    expect(ids).toEqual(expect.arrayContaining(accepted));
    expect(filterGraph(graph, '', 'rejected', '').nodes).toEqual([]);
    // Model and actor ids belong in the inspector, not under every node.
    expect(graph.nodes.some(n => /claude|sonnet|anthropic/i.test(n.detail))).toBe(false);
  });
});
