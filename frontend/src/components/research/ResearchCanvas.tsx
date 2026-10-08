/* eslint-disable react-hooks/refs -- `positions` is a mutable layout cache read during render;
   layoutVersion and layoutGraph tell the memos when it changed. Moving it into state would make
   every drag re-render the whole graph. Revisit if this component is restructured. */
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import {
  BaseEdge,
  Background,
  BackgroundVariant,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type EdgeProps,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react';
import { Button, cn } from '@cloudflare/kumo';
import { ArrowsClockwiseIcon, CornersOutIcon } from '@phosphor-icons/react';
import type { ResearchGraph, ResearchNode } from '../../lib/research/graph';
import {
  dagrePositions,
  readLayout,
  writeLayout,
  researchCentre,
  researchGlyphSize,
  researchSize,
  stableLayout,
  type SavedLayout,
  type XY,
} from '../../lib/research/layout';
import { ResearchGlyph, researchColor } from './ResearchGlyph';
import { verificationHighlights, type VerificationTrace } from '../../lib/research/verification';
import { useReducedMotion } from 'framer-motion';
type ArgumentNode = Node<
  {
    argument: ResearchNode;
    dimmed: boolean;
    highlighted?: boolean;
    check?: 'checking' | 'checked' | 'attention';
  },
  'argument'
>;
const GraphNode = memo(function GraphNode({ data, selected }: NodeProps<ArgumentNode>) {
  const node = data.argument;
  return (
    <div
      style={{ ...researchSize(node), opacity: data.dimmed ? 0.3 : 1 }}
      className="research-graph-node flex flex-col items-center pt-1 text-center"
      data-testid={`research-node-${node.id}`}
      title={node.label}
    >
      <div
        className={cn(
          'rounded-full transition-shadow duration-300',
          selected && 'outline outline-offset-4 outline-zinc-400',
          data.check === 'checking' && 'research-verification-active',
          data.check === 'checked' && 'outline outline-offset-4 outline-pass/40',
          data.check === 'attention' && 'outline outline-offset-4 outline-warn/70',
          data.highlighted && !data.check && 'outline-2 outline-offset-4 outline-blue-500',
        )}
      >
        <ResearchGlyph
          kind={node.kind}
          state={node.state}
          proposed={node.proposed}
          size={researchGlyphSize(node)}
        />
      </div>
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        style={{
          top: researchCentre(node),
          left: researchSize(node).width / 2 - researchGlyphSize(node) / 2,
        }}
      />
      <Handle
        type="source"
        position={Position.Right}
        id="out"
        style={{
          top: researchCentre(node),
          left: researchSize(node).width / 2 + researchGlyphSize(node) / 2,
        }}
      />
      <Handle
        type="source"
        position={Position.Left}
        id="back-out"
        style={{
          top: researchCentre(node),
          left: researchSize(node).width / 2 - researchGlyphSize(node) / 2,
        }}
      />
      <Handle
        type="target"
        position={Position.Right}
        id="back-in"
        style={{
          top: researchCentre(node),
          left: researchSize(node).width / 2 + researchGlyphSize(node) / 2,
        }}
      />
      <Handle
        type="source"
        position={Position.Top}
        id="above-out"
        style={{ top: 4, left: '50%' }}
      />
      <Handle type="target" position={Position.Top} id="above-in" style={{ top: 4, left: '50%' }} />
      <div
        className={cn(
          'mt-1.5 line-clamp-3 max-w-full bg-paper/90 px-1 text-xs leading-tight',
          node.kind === 'conclusion' && 'text-[15px] font-medium tracking-tight',
        )}
      >
        {node.label}
      </div>
      <div className="mt-1 line-clamp-3 max-w-full px-1 text-[9px] leading-tight text-zinc-500">
        {node.detail}
      </div>
      {data.check && (
        <span
          className={cn(
            'mt-1 text-[9px]',
            data.check === 'checking'
              ? 'text-running'
              : data.check === 'attention'
                ? 'text-warn'
                : 'text-pass',
          )}
        >
          {data.check === 'checking'
            ? 'Checking'
            : data.check === 'attention'
              ? 'Finding · needs review'
              : 'Checked'}
        </span>
      )}
    </div>
  );
});
function AboveEdge(props: EdgeProps) {
  const { sourceX, sourceY, targetX, targetY } = props;
  const top = Number(props.data?.routeY ?? Math.min(sourceY, targetY) - 64);
  const direction = sourceX < targetX ? 1 : -1;
  const radius = Math.min(12, Math.abs(targetX - sourceX) / 2);
  const path = `M ${sourceX} ${sourceY} L ${sourceX} ${top + radius} Q ${sourceX} ${top} ${sourceX + direction * radius} ${top} L ${targetX - direction * radius} ${top} Q ${targetX} ${top} ${targetX} ${top + radius} L ${targetX} ${targetY}`;
  return <BaseEdge {...props} path={path} labelX={(sourceX + targetX) / 2} labelY={top} />;
}
const edgeTypes = { above: AboveEdge };
const STRUCTURAL = new Set(['premise_of', 'concludes', 'grounds']);
// Below this zoom node text cannot be read, so the first view never zooms out further.
const READABLE_ZOOM = 0.6;
/** Where a workspace first opens: the top-most conclusion and its neighbours, else the top of the
 *  layout, at a readable zoom. "Fit argument" still shows everything on request. */
function openingFocus(graph: ResearchGraph, positions: Record<string, XY>) {
  const placed = graph.nodes
    .filter(n => positions[n.id])
    .sort((a, b) => positions[a.id].y - positions[b.id].y || positions[a.id].x - positions[b.id].x);
  const anchor = placed.find(n => n.kind === 'conclusion');
  const nodes = anchor
    ? [
        anchor.id,
        ...graph.edges.flatMap(e =>
          e.target === anchor.id ? [e.source] : e.source === anchor.id ? [e.target] : [],
        ),
      ]
    : placed.slice(0, 8).map(n => n.id);
  return { nodes: nodes.map(id => ({ id })), minZoom: READABLE_ZOOM };
}
const nodeTypes = { argument: GraphNode };
export function ResearchCanvas({
  graph,
  layoutGraph = graph,
  storageKey,
  selected,
  onSelect,
  verification,
  highlight,
}: {
  graph: ResearchGraph;
  layoutGraph?: ResearchGraph;
  storageKey?: string;
  selected?: string;
  onSelect: (id?: string) => void;
  verification?: VerificationTrace;
  highlight?: ReadonlySet<string>;
}) {
  const container = useRef<HTMLDivElement>(null);
  const positions = useRef<Record<string, XY>>({});
  const [flow, setFlow] = useState<ReactFlowInstance<ArgumentNode> | null>(null);
  const [hovered, setHovered] = useState<string>();
  const [layoutVersion, setLayoutVersion] = useState(0);
  const savedViewport = useRef<SavedLayout['viewport']>(undefined);
  const manual = useRef(false); // the person has dragged nodes into place
  const [ready, setReady] = useState(false);
  const initialFit = useRef(false);
  const lastHighlightFocus = useRef('');
  const lastCheckFocus = useRef('');
  const [tidying, setTidying] = useState(false);
  useEffect(() => {
    const saved = storageKey ? readLayout(storageKey) : { positions: {} };
    positions.current = saved.positions;
    savedViewport.current = saved.viewport;
    manual.current = saved.manual ?? false;
    initialFit.current = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- saved positions live in localStorage, readable only after mount
    setReady(true);
    setLayoutVersion(v => v + 1);
  }, [storageKey]);
  const reducedMotion = useReducedMotion();
  useMemo(() => {
    if (ready)
      positions.current = {
        ...positions.current,
        ...stableLayout(layoutGraph, positions.current, manual.current),
      };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- layoutVersion forces a relayout after saved positions load
  }, [layoutGraph, layoutVersion, ready]);
  useEffect(() => {
    if (ready && storageKey)
      writeLayout(storageKey, {
        positions: positions.current,
        viewport: savedViewport.current,
        manual: manual.current,
      });
  }, [storageKey, layoutGraph, layoutVersion, ready]);
  const highlights = useMemo(() => verificationHighlights(verification), [verification]);
  const checkingIds = useMemo(
    () =>
      new Set(
        graph.nodes
          .filter(
            node =>
              highlights.active.has(node.id) ||
              node.sourceIds.some(id => highlights.active.has(id)),
          )
          .map(node => node.id),
      ),
    [graph, highlights],
  );
  const nodes = useMemo(() => {
    if (!ready) return [];
    const focus = hovered ?? selected;
    const focusedEdge = graph.edges.find(e => e.id === focus);
    const neighbours = new Set([
      focus,
      ...(focusedEdge ? [focusedEdge.source, focusedEdge.target] : []),
      ...graph.nodes.filter(n => focus && n.sourceIds.includes(focus)).map(n => n.id),
      ...graph.edges.flatMap(e =>
        e.source === focus ? [e.target] : e.target === focus ? [e.source] : [],
      ),
    ]);
    const lit = highlight?.size ? highlight : undefined;
    return graph.nodes.map(n => ({
      id: n.id,
      type: 'argument' as const,
      position: positions.current[n.id],
      ...researchSize(n),
      selected: selected === n.id,
      data: {
        argument: n,
        highlighted: !!lit?.has(n.id),
        dimmed: focus ? !neighbours.has(n.id) : !!lit && !lit.has(n.id),
        check: checkingIds.has(n.id)
          ? ('checking' as const)
          : highlights.attention.has(n.id) || n.sourceIds.some(id => highlights.attention.has(id))
            ? ('attention' as const)
            : highlights.checked.has(n.id) || n.sourceIds.some(id => highlights.checked.has(id))
              ? ('checked' as const)
              : undefined,
      },
      ariaLabel: `${n.label}, ${n.state}`,
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- layoutGraph and layoutVersion signal that positions.current changed
  }, [
    graph,
    layoutGraph,
    ready,
    selected,
    hovered,
    layoutVersion,
    highlights,
    checkingIds,
    highlight,
  ]);
  const edges = useMemo(() => {
    let lane = 0;
    const top = Math.min(0, ...nodes.map(n => n.position.y)) - 64;
    return graph.edges.map(e => {
      const source = positions.current[e.source],
        target = positions.current[e.target];
      const above =
        e.relation === 'concerns' ||
        (source && target && source.x - target.x > 500 && e.relation !== 'blocks');
      const backwards = source && target && source.x > target.x;
      const checking = checkingIds.has(e.source) || checkingIds.has(e.target);
      const chained = !!highlight?.has(e.source) && !!highlight?.has(e.target);
      const focus = hovered ?? selected;
      // Structural links read from the arrows and shapes; their labels only crowd the canvas, so
      // they appear when either end is in focus or an audit has something to say.
      const labelled =
        !STRUCTURAL.has(e.relation) || !!e.auditVerdict || e.source === focus || e.target === focus;
      return {
        ...e,
        markerEnd: { type: MarkerType.ArrowClosed, color: '#a1a1aa', width: 14, height: 14 },
        type: above ? 'above' : 'default',
        data: { routeY: above ? top - lane++ * 24 : undefined },
        sourceHandle: above ? 'above-out' : backwards ? 'back-out' : 'out',
        targetHandle: above ? 'above-in' : backwards ? 'back-in' : 'in',
        label: labelled
          ? `${e.relation.replace(/_/g, ' ')}${e.auditVerdict === 'needs_review' ? ' · needs review' : e.auditVerdict === 'supported' ? ' · audited' : ''}`
          : undefined,
        animated: checking && !reducedMotion,
        style: {
          opacity: highlight?.size && !chained && !checking ? 0.25 : 1,
          stroke: checking
            ? '#60a5fa'
            : chained
              ? '#2563eb'
              : e.auditVerdict === 'needs_review'
                ? researchColor('warn')
                : e.relation === 'blocks' || e.relation === 'rebuts' || e.relation === 'undercuts'
                  ? researchColor('fail')
                  : '#a1a1aa',
          strokeWidth: checking ? 1.8 : chained ? 2 : 1,
          strokeDasharray: checking ? '5 5' : e.proposed ? '3 3' : undefined,
        },
        labelStyle: { fill: '#85858e', fontSize: 9 },
        labelBgStyle: { fill: '#fafaf9', fillOpacity: 0.96 },
        labelBgPadding: [6, 3] as [number, number],
      };
    });
  }, [graph, nodes, checkingIds, reducedMotion, highlight, hovered, selected]);
  useEffect(() => {
    if (!flow || !ready) return;
    if (!highlight?.size) {
      lastHighlightFocus.current = '';
      return;
    }
    const signature = [...highlight]
      .filter(id => graph.nodes.some(n => n.id === id))
      .sort()
      .join('|');
    if (lastHighlightFocus.current === signature) return;
    lastHighlightFocus.current = signature;
    const shown = graph.nodes.filter(node => highlight.has(node.id)).map(node => ({ id: node.id }));
    if (shown.length)
      void flow.fitView({
        nodes: shown,
        padding: 0.35,
        maxZoom: 1.1,
        duration: reducedMotion ? 0 : 450,
      });
  }, [flow, ready, highlight, graph.nodes, reducedMotion]);
  useEffect(() => {
    if (!flow || !ready) return;
    if (!checkingIds.size) {
      lastCheckFocus.current = '';
      return;
    }
    const focus = new Set(checkingIds);
    for (const edge of graph.edges)
      if (checkingIds.has(edge.source) || checkingIds.has(edge.target)) {
        focus.add(edge.source);
        focus.add(edge.target);
      }
    const signature = [...focus].sort().join('|');
    if (lastCheckFocus.current === signature) return;
    lastCheckFocus.current = signature;
    void flow.fitView({
      nodes: [...focus].map(id => ({ id })),
      padding: 0.35,
      maxZoom: 1.1,
      duration: reducedMotion ? 0 : 350,
    });
  }, [flow, ready, checkingIds, graph.edges, reducedMotion]);
  useEffect(() => {
    if (!flow || !ready || !nodes.length || initialFit.current) return;
    initialFit.current = true;
    if (savedViewport.current) void flow.setViewport(savedViewport.current);
    else
      void flow.fitView({ ...openingFocus(graph, positions.current), padding: 0.2, maxZoom: 0.9 });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only the first fit of a workspace
  }, [flow, ready, nodes]);
  useEffect(() => {
    if (!flow || !tidying) return;
    const frame = requestAnimationFrame(() => {
      void flow.fitView({ padding: 0.2, maxZoom: 1.1, duration: reducedMotion ? 0 : 350 });
      setTidying(false);
    });
    return () => cancelAnimationFrame(frame);
  }, [flow, tidying, reducedMotion]);
  function tidy() {
    manual.current = false; // tidying hands the layout back to the automatic one
    positions.current = dagrePositions(layoutGraph);
    setLayoutVersion(v => v + 1);
    setTidying(true);
  }
  return (
    <div
      ref={container}
      className={cn('research-canvas absolute inset-0', selected && 'min-[900px]:right-[350px]')}
      data-testid="research-canvas"
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onInit={setFlow}
        onNodeClick={(_, node) => onSelect(node.id)}
        onEdgeClick={(_, edge) =>
          onSelect(
            !['grounds', 'premise_of', 'concludes', 'blocks'].includes(
              graph.edges.find(e => e.id === edge.id)?.relation ?? '',
            )
              ? edge.id
              : edge.source,
          )
        }
        onPaneClick={() => onSelect()}
        onNodeMouseEnter={(_, n) => setHovered(n.id)}
        onNodeMouseLeave={() => setHovered(undefined)}
        onNodesChange={changes => {
          let moved = false;
          for (const change of changes)
            if (change.type === 'position' && change.position) {
              const prior = positions.current[change.id];
              if (!prior || prior.x !== change.position.x || prior.y !== change.position.y) {
                positions.current[change.id] = change.position;
                moved = true;
              }
            }
          if (moved) setLayoutVersion(v => v + 1);
        }}
        onNodeDragStop={(_, n) => {
          positions.current[n.id] = n.position;
          manual.current = true;
          if (storageKey)
            writeLayout(storageKey, {
              positions: positions.current,
              viewport: savedViewport.current,
              manual: true,
            });
        }}
        onMoveEnd={(_, viewport) => {
          savedViewport.current = viewport;
          if (ready && storageKey)
            writeLayout(storageKey, {
              positions: positions.current,
              viewport,
              manual: manual.current,
            });
        }}
        minZoom={0.15}
        maxZoom={2}
        nodesConnectable={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={0.65} color="#dedee2" />
      </ReactFlow>
      <Button
        variant="outline"
        size="sm"
        className="absolute bottom-5 left-5 text-[10px]"
        icon={<CornersOutIcon size={14} />}
        onClick={() => void flow?.fitView({ padding: 0.2, maxZoom: 1.1 })}
      >
        Fit argument
      </Button>
      <Button
        variant="outline"
        size="sm"
        className="absolute bottom-5 right-5 text-[10px]"
        icon={<ArrowsClockwiseIcon size={14} />}
        disabled={!nodes.length}
        onClick={tidy}
      >
        Tidy layout
      </Button>
    </div>
  );
}
