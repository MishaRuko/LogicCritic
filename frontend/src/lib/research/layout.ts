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
  for (const node of [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id))) layout.setNode(node.id, researchSize(node));
  for (const edge of [...graph.edges].sort((a, b) => a.id.localeCompare(b.id))) {
    if (!layout.hasNode(edge.source) || !layout.hasNode(edge.target) || edge.relation === 'concerns') continue;
    // An obligation is laid out after the object it blocks so it reads as attached to it, not as evidence.
    if (edge.relation === 'blocks') layout.setEdge(edge.target, edge.source, { weight: 2, minlen: 1 });
    else layout.setEdge(edge.source, edge.target, { weight: ['grounds', 'premise_of', 'concludes'].includes(edge.relation) ? 3 : 1 });
  }
  dagre.layout(layout);
  return Object.fromEntries(graph.nodes.map(n => { const p = layout.node(n.id); const s = researchSize(n); return [n.id, { x: Math.round(p.x - s.width / 2), y: Math.round(p.y - s.height / 2) }]; }));
}
const overlaps = (a: XY & { width: number; height: number }, b: XY & { width: number; height: number }, gap = 32) => a.x < b.x + b.width + gap && a.x + a.width + gap > b.x && a.y < b.y + b.height + gap && a.y + a.height + gap > b.y;
/** Keep existing nodes fixed; place additions near the centre of their connected neighbours. */
export function stableLayout(graph: ResearchGraph, previous: Record<string, XY> = {}): Record<string, XY> {
  const fresh = dagrePositions(graph); const placed: Record<string, XY> = {};
  const sizes = new Map(graph.nodes.map(n => [n.id, researchSize(n)]));
  const rects = () => Object.entries(placed).map(([id, p]) => ({ ...p, ...sizes.get(id)! }));
  for (const n of graph.nodes) if (previous[n.id]) placed[n.id] = previous[n.id];
  if (!Object.keys(placed).length) return fresh;
  const pending = graph.nodes.filter(n => !placed[n.id]);
  while (pending.length) {
    const neighbours = (id: string) => [...new Set(graph.edges.flatMap(e => e.source === id ? [e.target] : e.target === id ? [e.source] : []))].filter(id => placed[id]);
    // Grow a new chain from its existing anchor.
    pending.sort((a, b) => neighbours(b.id).length - neighbours(a.id).length || fresh[a.id].x - fresh[b.id].x || a.id.localeCompare(b.id));
    const n = pending.shift()!; const anchors = neighbours(n.id);
    const shift = anchors.length ? {
      x: anchors.reduce((sum, id) => sum + placed[id].x - fresh[id].x, 0) / anchors.length,
      y: anchors.reduce((sum, id) => sum + placed[id].y - fresh[id].y, 0) / anchors.length,
    } : { x: 0, y: Math.max(...rects().map(r => r.y + r.height)) + 64 - fresh[n.id].y };
    const size = sizes.get(n.id)!;
    const ideal = { x: Math.round(fresh[n.id].x + shift.x), y: Math.round(fresh[n.id].y + shift.y) };
    // Search above and below without a fixed limit that can leave overlapping nodes.
    let at = ideal;
    for (let distance = 1; rects().some(r => overlaps(r, { ...at, ...size })); distance++) {
      at = { x: ideal.x, y: ideal.y + Math.ceil(distance / 2) * 32 * (distance % 2 ? 1 : -1) };
    }
    placed[n.id] = at;
  }
  return placed;
}
export interface SavedLayout { positions: Record<string, XY>; viewport?: XY & { zoom: number } }
const layoutKey = (key: string) => `trial:graph-layout:v1:${key}`;
export function readLayout(key: string): SavedLayout {
  try {
    const data = JSON.parse(localStorage.getItem(layoutKey(key)) ?? '{}');
    const validXY = (p: XY) => p && Number.isFinite(p.x) && Number.isFinite(p.y);
    const positions = Object.fromEntries(Object.entries(data.positions ?? {}).filter(([, p]) => validXY(p as XY)));
    const viewport = validXY(data.viewport) && Number.isFinite(data.viewport.zoom) && data.viewport.zoom >= .15 && data.viewport.zoom <= 2 ? data.viewport : undefined;
    return { positions: positions as Record<string, XY>, viewport };
  } catch { return { positions: {} }; }
}
export function writeLayout(key: string, value: SavedLayout) {
  try { localStorage.setItem(layoutKey(key), JSON.stringify(value)); } catch { /* Continue when browser storage is unavailable. */ }
}
export function layoutBounds(graph: ResearchGraph, positions: Record<string, XY>) {
  const rects = graph.nodes.filter(n => positions[n.id]).map(n => ({ ...positions[n.id], ...researchSize(n) }));
  if (!rects.length) return { x: 0, y: 0, width: 600, height: 400 };
  const x = Math.min(...rects.map(r => r.x)) - 40, y = Math.min(...rects.map(r => r.y)) - 40;
  return { x, y, width: Math.max(...rects.map(r => r.x + r.width)) - x + 40, height: Math.max(...rects.map(r => r.y + r.height)) - y + 60 };
}
