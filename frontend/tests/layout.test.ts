import { beforeEach, describe, expect, it } from 'vitest';
import type { ResearchGraph, ResearchNode } from '../src/lib/research/graph';
import {
  dagrePositions,
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
    localStorage.setItem('trial:graph-layout:v1:broken', '{bad');
    expect(readLayout('broken').positions).toEqual({});
    localStorage.setItem(
      'trial:graph-layout:v1:invalid',
      JSON.stringify({
        positions: { valid: { x: 0, y: 20 }, invalid: { x: 'bad', y: 10 } },
        viewport: { x: 0, y: 0, zoom: -1 },
      }),
    );
    expect(readLayout('invalid')).toEqual({
      positions: { valid: { x: 0, y: 20 } },
      viewport: undefined,
    });
  });
});
