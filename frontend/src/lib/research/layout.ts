import dagre from '@dagrejs/dagre';
import type { ResearchGraph, ResearchKind, ResearchNode } from './graph';
export type XY = { x: number; y: number };
// Glyph plus a label beneath; conclusions carry the larger glyph and title.
export function researchSize(node: Pick<ResearchNode, 'kind'>) {
  return node.kind === 'conclusion' ? { width: 220, height: 140 } : { width: 180, height: 132 };
}
const glyphSizes: Record<ResearchKind, number> = {
  conclusion: 34,
  goal: 28,
  statement: 22,
  step: 22,
  obligation: 22,
  excerpt: 16,
};
export const researchGlyphSize = (node: Pick<ResearchNode, 'kind'>) => glyphSizes[node.kind];
export const researchCentre = (node: Pick<ResearchNode, 'kind'>) => 4 + researchGlyphSize(node) / 2;
const CLUSTER_GAP = 96;
const STEP = 32;

/** Groups of nodes connected to each other, largest first (ties broken by id, so stable). */
function clusters(graph: ResearchGraph): string[][] {
  const parent = new Map(graph.nodes.map(n => [n.id, n.id]));
  const root = (id: string): string => {
    while (parent.get(id) !== id) id = parent.get(id)!;
    return id;
  };
  for (const e of graph.edges)
    if (parent.has(e.source) && parent.has(e.target)) parent.set(root(e.source), root(e.target));
  const groups = new Map<string, string[]>();
  for (const n of graph.nodes) groups.set(root(n.id), [...(groups.get(root(n.id)) ?? []), n.id]);
  return [...groups.values()]
    .map(ids => ids.sort())
    .sort((a, b) => b.length - a.length || a[0].localeCompare(b[0]));
}

/** Dagre left→right for one connected group: evidence, then statements, reasoning steps and
 *  conclusions; obligations sit beside what they block. Positions start at (0, 0). */
function dagreCluster(graph: ResearchGraph, ids: string[]): Record<string, XY> {
  const inside = new Set(ids);
  const layout = new dagre.graphlib.Graph({ multigraph: false })
    .setGraph({ rankdir: 'LR', nodesep: 64, ranksep: 100, marginx: 0, marginy: 0 })
    .setDefaultEdgeLabel(() => ({}));
  const nodes = graph.nodes.filter(n => inside.has(n.id)).sort((a, b) => a.id.localeCompare(b.id));
  for (const node of nodes) layout.setNode(node.id, researchSize(node));
  for (const edge of [...graph.edges].sort((a, b) => a.id.localeCompare(b.id))) {
    if (!inside.has(edge.source) || !inside.has(edge.target) || edge.relation === 'concerns')
      continue;
    // An obligation is laid out after the object it blocks so it reads as attached to it, not as evidence.
    if (edge.relation === 'blocks')
      layout.setEdge(edge.target, edge.source, { weight: 2, minlen: 1 });
    else
      layout.setEdge(edge.source, edge.target, {
        weight: ['grounds', 'premise_of', 'concludes'].includes(edge.relation) ? 3 : 1,
      });
  }
  dagre.layout(layout);
  const placed = Object.fromEntries(
    nodes.map(n => {
      const p = layout.node(n.id);
      const s = researchSize(n);
      return [n.id, { x: Math.round(p.x - s.width / 2), y: Math.round(p.y - s.height / 2) }];
    }),
  );
  const left = Math.min(...Object.values(placed).map(p => p.x));
  const top = Math.min(...Object.values(placed).map(p => p.y));
  return Object.fromEntries(
    Object.entries(placed).map(([id, p]) => [id, { x: p.x - left, y: p.y - top }]),
  );
}

/** Lay out each connected group with dagre, then pack the groups into rows.
 *
 *  Laid out as one graph, every unconnected claim lands in dagre's first column, so a workspace
 *  of mostly separate claims became a strip many times taller than wide and fitting it on screen
 *  shrank everything. Packing keeps the graph roughly as wide as it is tall. */
export function dagrePositions(graph: ResearchGraph): Record<string, XY> {
  const byId = new Map(graph.nodes.map(n => [n.id, n]));
  const laid = clusters(graph).map(ids => {
    const positions = dagreCluster(graph, ids);
    const width = Math.max(...ids.map(id => positions[id].x + researchSize(byId.get(id)!).width));
    const height = Math.max(...ids.map(id => positions[id].y + researchSize(byId.get(id)!).height));
    return { positions, width, height };
  });
  const area = laid.reduce((sum, c) => sum + (c.width + CLUSTER_GAP) * (c.height + CLUSTER_GAP), 0);
  const rowWidth = Math.max(...laid.map(c => c.width), Math.min(4000, Math.sqrt(area * 1.6)));
  const result: Record<string, XY> = {};
  let x = 0;
  let y = 0;
  let rowHeight = 0;
  for (const cluster of laid) {
    if (x > 0 && x + cluster.width > rowWidth) {
      x = 0;
      y += rowHeight + CLUSTER_GAP;
      rowHeight = 0;
    }
    for (const [id, p] of Object.entries(cluster.positions))
      result[id] = { x: p.x + x + 30, y: p.y + y + 30 };
    x += cluster.width + CLUSTER_GAP;
    rowHeight = Math.max(rowHeight, cluster.height);
  }
  return result;
}
const overlaps = (
  a: XY & { width: number; height: number },
  b: XY & { width: number; height: number },
  gap = 32,
) =>
  a.x < b.x + b.width + gap &&
  a.x + a.width + gap > b.x &&
  a.y < b.y + b.height + gap &&
  a.y + a.height + gap > b.y;

/** The free spot nearest to `ideal`. Vertical moves cost half as much as horizontal ones, so a
 *  node stays in its column of the argument when it can. */
function nearestFree(
  ideal: XY,
  size: { width: number; height: number },
  taken: (XY & { width: number; height: number })[],
): XY {
  const free = (at: XY) => !taken.some(r => overlaps(r, { ...at, ...size }));
  if (free(ideal)) return ideal;
  for (let ring = 1; ring <= 200; ring++) {
    const candidates: XY[] = [];
    for (let dx = -ring; dx <= ring; dx++)
      for (let dy = -ring; dy <= ring; dy++)
        if (Math.max(Math.abs(dx), Math.abs(dy)) === ring)
          candidates.push({ x: ideal.x + dx * STEP, y: ideal.y + dy * STEP });
    candidates.sort(
      (a, b) =>
        2 * Math.abs(a.x - ideal.x) +
          Math.abs(a.y - ideal.y) -
          (2 * Math.abs(b.x - ideal.x) + Math.abs(b.y - ideal.y)) ||
        a.y - b.y ||
        a.x - b.x,
    );
    const spot = candidates.find(free);
    if (spot) return spot;
  }
  // Nothing free nearby (a very dense canvas): go below everything.
  return { x: ideal.x, y: Math.max(...taken.map(r => r.y + r.height)) + 64 };
}

/** How tangled a layout looks: crossing links, and links drawn through other nodes (counted
 *  double). Links are taken as straight lines between node centres, which is close enough to
 *  compare two layouts of the same graph. */
export function layoutMess(graph: ResearchGraph, positions: Record<string, XY>): number {
  const box = new Map(
    graph.nodes
      .filter(n => positions[n.id])
      .map(n => [n.id, { ...positions[n.id], ...researchSize(n) }]),
  );
  const centre = (id: string) => {
    const b = box.get(id)!;
    return { x: b.x + b.width / 2, y: b.y + 20 };
  };
  const links = graph.edges
    .filter(e => box.has(e.source) && box.has(e.target))
    .map(e => ({ e, a: centre(e.source), b: centre(e.target) }));
  const side = (a: XY, b: XY, c: XY) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  let mess = 0;
  for (let i = 0; i < links.length; i++)
    for (let j = i + 1; j < links.length; j++) {
      const [p, q, r, s] = [links[i].a, links[i].b, links[j].a, links[j].b];
      if (side(p, q, r) * side(p, q, s) < 0 && side(r, s, p) * side(r, s, q) < 0) mess += 1;
    }
  for (const { e, a, b } of links)
    for (const [id, r] of box) {
      if (id === e.source || id === e.target) continue;
      for (let t = 0.05; t < 0.95; t += 0.05) {
        const x = a.x + (b.x - a.x) * t;
        const y = a.y + (b.y - a.y) * t;
        if (x > r.x + 20 && x < r.x + r.width - 20 && y > r.y && y < r.y + r.height - 30) {
          mess += 2;
          break;
        }
      }
    }
  return mess;
}

/** Keep existing nodes fixed; place additions near their connected neighbours.
 *
 *  Kept positions keep a growing graph readable while it grows, but a new link between groups
 *  placed apart can tangle it. Unless a person has arranged the nodes (`manual`), a layout that
 *  growth has made clearly messier than a fresh one is replaced by the fresh one. */
export function stableLayout(
  graph: ResearchGraph,
  previous: Record<string, XY> = {},
  manual = false,
): Record<string, XY> {
  const fresh = dagrePositions(graph);
  const placed: Record<string, XY> = {};
  const sizes = new Map(graph.nodes.map(n => [n.id, researchSize(n)]));
  const rects = () => Object.entries(placed).map(([id, p]) => ({ ...p, ...sizes.get(id)! }));
  for (const n of graph.nodes) if (previous[n.id]) placed[n.id] = previous[n.id];
  if (!Object.keys(placed).length) return fresh;
  // How far the kept layout sits from a fresh one, for additions with nothing to anchor to.
  const kept = Object.keys(placed);
  const median = (values: number[]) => values.sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const drift = {
    x: median(kept.map(id => placed[id].x - fresh[id].x)),
    y: median(kept.map(id => placed[id].y - fresh[id].y)),
  };
  const pending = graph.nodes.filter(n => !placed[n.id]);
  while (pending.length) {
    const neighbours = (id: string) =>
      [
        ...new Set(
          graph.edges.flatMap(e =>
            e.source === id ? [e.target] : e.target === id ? [e.source] : [],
          ),
        ),
      ].filter(id => placed[id]);
    // Grow a new chain from its existing anchor.
    pending.sort(
      (a, b) =>
        neighbours(b.id).length - neighbours(a.id).length ||
        fresh[a.id].x - fresh[b.id].x ||
        a.id.localeCompare(b.id),
    );
    const n = pending.shift()!;
    const anchors = neighbours(n.id);
    const shift = anchors.length
      ? {
          x: anchors.reduce((sum, id) => sum + placed[id].x - fresh[id].x, 0) / anchors.length,
          y: anchors.reduce((sum, id) => sum + placed[id].y - fresh[id].y, 0) / anchors.length,
        }
      : drift;
    const ideal = {
      x: Math.round(fresh[n.id].x + shift.x),
      y: Math.round(fresh[n.id].y + shift.y),
    };
    placed[n.id] = nearestFree(ideal, sizes.get(n.id)!, rects());
  }
  const added = graph.nodes.length > kept.length;
  if (!manual && added && layoutMess(graph, placed) > layoutMess(graph, fresh) * 1.5 + 2)
    return fresh;
  return placed;
}
export interface SavedLayout {
  positions: Record<string, XY>;
  viewport?: XY & { zoom: number };
  /** A person dragged nodes; their arrangement is kept rather than tidied automatically. */
  manual?: boolean;
}
// v2: clusters are packed into rows; v1 layouts were one tall column and are not reused.
const layoutKey = (key: string) => `trial:graph-layout:v2:${key}`;
export function readLayout(key: string): SavedLayout {
  try {
    const data = JSON.parse(localStorage.getItem(layoutKey(key)) ?? '{}');
    const validXY = (p: XY) => p && Number.isFinite(p.x) && Number.isFinite(p.y);
    const positions = Object.fromEntries(
      Object.entries(data.positions ?? {}).filter(([, p]) => validXY(p as XY)),
    );
    const viewport =
      validXY(data.viewport) &&
      Number.isFinite(data.viewport.zoom) &&
      data.viewport.zoom >= 0.15 &&
      data.viewport.zoom <= 2
        ? data.viewport
        : undefined;
    return {
      positions: positions as Record<string, XY>,
      viewport,
      ...(data.manual === true ? { manual: true } : {}),
    };
  } catch {
    return { positions: {} };
  }
}
export function writeLayout(key: string, value: SavedLayout) {
  try {
    localStorage.setItem(layoutKey(key), JSON.stringify(value));
  } catch {
    /* Continue when browser storage is unavailable. */
  }
}
