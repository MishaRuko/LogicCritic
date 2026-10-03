import dagre from '@dagrejs/dagre';
import type { ResearchGraph, ResearchKind, ResearchNode } from './graph';
export type XY = { x: number; y: number };
// Glyph plus a label beneath; conclusions carry the larger glyph and title.
export function researchSize(node: Pick<ResearchNode, 'kind'>) {
  return node.kind === 'conclusion' ? { width: 220, height: 140 } : { width: 180, height: 132 };
}
const glyphSizes: Record<ResearchKind, number> = { conclusion: 34, goal: 28, statement: 22, step: 22, obligation: 22, excerpt: 16 };
export const researchGlyphSize = (node: Pick<ResearchNode, 'kind'>) => glyphSizes[node.kind];
export const researchCentre = (node: Pick<ResearchNode, 'kind'>) => 4 + researchGlyphSize(node) / 2;
/** Dagre left→right: evidence, then statements, reasoning steps and conclusions; obligations sit beside what they block. */
export function dagrePositions(graph: ResearchGraph): Record<string, XY> {
  const layout = new dagre.graphlib.Graph({ multigraph: false }).setGraph({ rankdir: 'LR', nodesep: 64, ranksep: 100, marginx: 30, marginy: 30 }).setDefaultEdgeLabel(() => ({}));
  for (const node of graph.nodes) layout.setNode(node.id, researchSize(node));
  for (const edge of graph.edges) {
    if (!layout.hasNode(edge.source) || !layout.hasNode(edge.target) || edge.relation === 'concerns') continue;
    // An obligation is laid out after the object it blocks so it reads as attached to it, not as evidence.
    if (edge.relation === 'blocks') layout.setEdge(edge.target, edge.source, { weight: 2, minlen: 1 });
    else layout.setEdge(edge.source, edge.target, { weight: ['grounds', 'premise_of', 'concludes'].includes(edge.relation) ? 3 : 1 });
  }
  dagre.layout(layout);
  return Object.fromEntries(graph.nodes.map(n => { const p = layout.node(n.id); const s = researchSize(n); return [n.id, { x: Math.round(p.x - s.width / 2), y: Math.round(p.y - s.height / 2) }]; }));
}
const overlaps = (a: XY & { width: number; height: number }, b: XY & { width: number; height: number }, gap = 32) => a.x < b.x + b.width + gap && a.x + a.width + gap > b.x && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
/**
 * Positions that never move once assigned. New nodes take their Dagre position translated by the offset of an
 * already-placed neighbour, then step down until they are clear of every placed node.
 */
export function stableLayout(graph: ResearchGraph, previous: Record<string, XY> = {}): Record<string, XY> {
  const fresh = dagrePositions(graph); const placed: Record<string, XY> = {};
  const sizes = new Map(graph.nodes.map(n => [n.id, researchSize(n)]));
  const rects = () => Object.entries(placed).map(([id, p]) => ({ ...p, ...sizes.get(id)! }));
  for (const n of graph.nodes) if (previous[n.id]) placed[n.id] = previous[n.id];
  if (!Object.keys(placed).length) return fresh;
  const bottom = Math.max(...rects().map(r => r.y + r.height));
  // Newly arrived nodes are placed in dependency order so later ones can borrow their neighbours' offsets.
  const pending = graph.nodes.filter(n => !placed[n.id]).sort((a, b) => fresh[a.id].x - fresh[b.id].x || fresh[a.id].y - fresh[b.id].y);
  for (const n of pending) {
    const neighbour = graph.edges.map(e => e.source === n.id ? e.target : e.target === n.id ? e.source : undefined).find(id => id && placed[id]);
    const shift = neighbour ? { x: placed[neighbour].x - fresh[neighbour].x, y: placed[neighbour].y - fresh[neighbour].y } : { x: 0, y: bottom + 60 - Math.min(...pending.map(p => fresh[p.id].y)) };
    const size = sizes.get(n.id)!; const at = { x: fresh[n.id].x + shift.x, y: fresh[n.id].y + shift.y };
    for (let i = 0; i < 200 && rects().some(r => overlaps(r, { ...at, ...size })); i++) at.y += 24;
    placed[n.id] = at;
  }
  return placed;
}
export function layoutBounds(graph: ResearchGraph, positions: Record<string, XY>) {
  const rects = graph.nodes.filter(n => positions[n.id]).map(n => ({ ...positions[n.id], ...researchSize(n) }));
  if (!rects.length) return { x: 0, y: 0, width: 600, height: 400 };
  const x = Math.min(...rects.map(r => r.x)) - 40, y = Math.min(...rects.map(r => r.y)) - 40;
  return { x, y, width: Math.max(...rects.map(r => r.x + r.width)) - x + 40, height: Math.max(...rects.map(r => r.y + r.height)) - y + 60 };
}
