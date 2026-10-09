import { beforeEach, describe, expect, it } from 'vitest';
import type { ResearchGraph, ResearchNode } from '../src/lib/research/graph';
import {
  dagrePositions,
  layoutMess,
  readLayout,
  researchSize,
  stableLayout,
  writeLayout,
  type XY,
} from '../src/lib/research/layout';
const node = (id: string, kind: ResearchNode['kind'] = 'statement'): ResearchNode => ({
  id,
  kind,
  label: id,
  detail: '',
  state: 'idle',
  proposed: false,
  sourceIds: [],
});
const graph: ResearchGraph = {
  nodes: [node('a'), node('step', 'step'), node('answer', 'conclusion')],
  edges: [
    { id: '1', source: 'a', target: 'step', relation: 'premise_of' },
    { id: '2', source: 'step', target: 'answer', relation: 'concludes' },
  ],
};
beforeEach(() => localStorage.clear());
describe('graph layout stability', () => {
  it('lays out evidence before reasoning and conclusions regardless of API ordering', () => {
    const positions = dagrePositions(graph);
    expect(positions.a.x).toBeLessThan(positions.step.x);
    expect(positions.step.x).toBeLessThan(positions.answer.x);
    expect(
      dagrePositions({ nodes: [...graph.nodes].reverse(), edges: [...graph.edges].reverse() }),
    ).toEqual(positions);
  });
  it('preserves dragged nodes and places new evidence without overlap', () => {
    const positions: Record<string, XY> = { ...dagrePositions(graph), a: { x: -180, y: 200 } };
    const expanded = {
      nodes: [...graph.nodes, ...Array.from({ length: 12 }, (_, i) => node(`new-${i}`))],
      edges: [
        ...graph.edges,
        ...Array.from({ length: 12 }, (_, i) => ({
          id: `link-${i}`,
          source: `new-${i}`,
          target: 'step',
          relation: 'premise_of',
        })),
      ],
    };
    const next = stableLayout(expanded, positions);
    for (const n of graph.nodes) expect(next[n.id]).toEqual(positions[n.id]);
    for (const a of expanded.nodes)
      for (const b of expanded.nodes) {
        if (a.id === b.id) continue;
        const ap = next[a.id],
          bp = next[b.id],
          as = researchSize(a),
          bs = researchSize(b);
        expect(
          ap.x < bp.x + bs.width &&
            ap.x + as.width > bp.x &&
            ap.y < bp.y + bs.height &&
            ap.y + as.height > bp.y,
        ).toBe(false);
      }
  });
  it('saves positions and viewport separately for each workspace and view', () => {
    const saved = { positions: { a: { x: -50, y: 75 } }, viewport: { x: 30, y: -40, zoom: 0.6 } };
    writeLayout('workspace:overview', saved);
    expect(readLayout('workspace:overview')).toEqual(saved);
    expect(readLayout('other:overview').positions).toEqual({});
    expect(readLayout('workspace:evidence').positions).toEqual({});
    expect(stableLayout(graph, readLayout('workspace:overview').positions).a).toEqual(
      saved.positions.a,
    );
  });
  it('recovers from malformed storage and ignores invalid coordinates', () => {
    localStorage.setItem('trial:graph-layout:v2:broken', '{bad');
    expect(readLayout('broken').positions).toEqual({});
    localStorage.setItem(
      'trial:graph-layout:v2:invalid',
      JSON.stringify({
        positions: { valid: { x: 0, y: 20 }, invalid: { x: 'bad', y: 10 } },
        viewport: { x: 0, y: 0, zoom: -1 },
      }),
    );
    expect(readLayout('invalid')).toEqual({
      positions: { valid: { x: 0, y: 20 } },
      viewport: undefined,
    });
    // Layouts saved before clusters were packed are not reused.
    localStorage.setItem(
      'trial:graph-layout:v1:old',
      JSON.stringify({ positions: { a: { x: 0, y: 9000 } } }),
    );
    expect(readLayout('old').positions).toEqual({});
  });
});

describe('layout of larger graphs', () => {
  const box = (positions: Record<string, XY>, g: ResearchGraph) => {
    const rects = g.nodes.map(n => ({ ...positions[n.id], ...researchSize(n) }));
    return {
      width: Math.max(...rects.map(r => r.x + r.width)) - Math.min(...rects.map(r => r.x)),
      height: Math.max(...rects.map(r => r.y + r.height)) - Math.min(...rects.map(r => r.y)),
    };
  };

  it('packs unconnected claims into rows instead of one tall column', () => {
    const scattered: ResearchGraph = {
      nodes: Array.from({ length: 40 }, (_, i) => node(`claim-${String(i).padStart(2, '0')}`)),
      edges: [],
    };
    const { width, height } = box(dagrePositions(scattered), scattered);
    expect(height / width).toBeLessThan(2); // one column would be about 40 times taller
  });

  it('keeps an argument chain intact while packing the loose claims around it', () => {
    const mixed: ResearchGraph = {
      nodes: [...graph.nodes, node('loose-1'), node('loose-2')],
      edges: graph.edges,
    };
    const positions = dagrePositions(mixed);
    expect(positions.a.x).toBeLessThan(positions.step.x);
    expect(positions.step.x).toBeLessThan(positions.answer.x);
  });

  it('keeps a person’s arrangement, and tidies a tangled automatic one', () => {
    // Previous positions that cross every link: answer on the left, evidence on the right.
    const tangled: Record<string, XY> = {
      a: { x: 2000, y: 0 },
      step: { x: 1000, y: 600 },
      answer: { x: 0, y: 0 },
    };
    const grown: ResearchGraph = {
      nodes: [...graph.nodes, node('b')],
      edges: [...graph.edges, { id: '3', source: 'b', target: 'step', relation: 'premise_of' }],
    };
    const automatic = stableLayout(grown, tangled);
    expect(layoutMess(grown, automatic)).toBeLessThanOrEqual(
      layoutMess(grown, dagrePositions(grown)),
    );
    const arranged = stableLayout(grown, tangled, true);
    expect(arranged.a).toEqual(tangled.a);
    expect(arranged.answer).toEqual(tangled.answer);
  });
});

describe('readable layout of researched arguments', () => {
  const premises = Array.from({ length: 9 }, (_, i) => node(`p${i}`));
  const wide: ResearchGraph = {
    nodes: [...premises, node('step', 'step'), node('answer', 'conclusion')],
    edges: [
      ...premises.map(p => ({
        id: `e-${p.id}`,
        source: p.id,
        target: 'step',
        relation: 'premise_of',
      })),
      { id: 'c', source: 'step', target: 'answer', relation: 'concludes' },
    ],
  };
  it('wraps a column of many premises instead of stacking it out of sight', () => {
    const positions = dagrePositions(wide);
    const columns = new Set(premises.map(p => positions[p.id].x));
    expect(columns.size).toBe(3); // nine premises in three columns of three
    const premiseRight = Math.max(...premises.map(p => positions[p.id].x));
    expect(premiseRight).toBeLessThan(positions.step.x); // still evidence, then reasoning
    expect(positions.step.x).toBeLessThan(positions.answer.x);
  });
  it('does not let links across sources move the argument', () => {
    const linked = {
      ...wide,
      edges: [
        ...wide.edges,
        { id: 'x1', source: 'p0', target: 'p8', relation: 'rebuts' },
        { id: 'x2', source: 'p3', target: 'answer', relation: 'supports' },
      ],
    };
    expect(dagrePositions(linked)).toEqual(dagrePositions(wide));
  });
});
