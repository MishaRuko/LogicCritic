import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { BaseEdge, Background, BackgroundVariant, Handle, Position, ReactFlow, type EdgeProps, type Node, type NodeProps, type ReactFlowInstance } from '@xyflow/react';
import { Button, cn } from '@cloudflare/kumo';
import { CornersOutIcon } from '@phosphor-icons/react';
import type { ResearchGraph, ResearchNode } from '../../lib/research/graph';
import { researchCentre, researchGlyphSize, researchSize, stableLayout, type XY } from '../../lib/research/layout';
import { ResearchGlyph, researchColor } from './ResearchGlyph';
type ArgumentNode = Node<{ argument: ResearchNode; dimmed: boolean }, 'argument'>;
const GraphNode = memo(function GraphNode({ data, selected }: NodeProps<ArgumentNode>) {
  const node = data.argument;
  return <div style={{ ...researchSize(node), opacity: data.dimmed ? .3 : 1 }} className="research-graph-node flex flex-col items-center pt-1 text-center" data-testid={`research-node-${node.id}`} title={node.label}>
    <div className={cn('rounded-full', selected && 'outline outline-offset-4 outline-zinc-400')}><ResearchGlyph kind={node.kind} state={node.state} proposed={node.proposed} size={researchGlyphSize(node)}/></div>
    <Handle type="target" position={Position.Left} id="in" style={{ top: researchCentre(node), left: researchSize(node).width / 2 - researchGlyphSize(node) / 2 }}/>
    <Handle type="source" position={Position.Right} id="out" style={{ top: researchCentre(node), left: researchSize(node).width / 2 + researchGlyphSize(node) / 2 }}/>
    <Handle type="source" position={Position.Left} id="back-out" style={{ top: researchCentre(node), left: researchSize(node).width / 2 - researchGlyphSize(node) / 2 }}/>
    <Handle type="target" position={Position.Right} id="back-in" style={{ top: researchCentre(node), left: researchSize(node).width / 2 + researchGlyphSize(node) / 2 }}/>
    <Handle type="source" position={Position.Top} id="above-out" style={{ top: 4, left: '50%' }}/>
    <Handle type="target" position={Position.Top} id="above-in" style={{ top: 4, left: '50%' }}/>
    <div className={cn('mt-1.5 line-clamp-3 max-w-full bg-paper/90 px-1 text-xs leading-tight', node.kind === 'conclusion' && 'text-[15px] font-medium tracking-tight')}>{node.label}</div>
    <div className="mt-1 line-clamp-3 max-w-full px-1 text-[9px] leading-tight text-zinc-500">{node.proposed ? 'Needs review · ' : ''}{node.detail}</div>
  </div>;
});
function AboveEdge(props: EdgeProps) {
  const { sourceX, sourceY, targetX, targetY } = props;
  const top = Number(props.data?.routeY ?? Math.min(sourceY, targetY) - 64);
  const direction = sourceX < targetX ? 1 : -1;
  const radius = Math.min(12, Math.abs(targetX - sourceX) / 2);
  const path = `M ${sourceX} ${sourceY} L ${sourceX} ${top + radius} Q ${sourceX} ${top} ${sourceX + direction * radius} ${top} L ${targetX - direction * radius} ${top} Q ${targetX} ${top} ${targetX} ${top + radius} L ${targetX} ${targetY}`;
  return <BaseEdge {...props} path={path} labelX={(sourceX + targetX) / 2} labelY={top}/>;
}
const edgeTypes = { above: AboveEdge };
const nodeTypes = { argument: GraphNode };
export function ResearchCanvas({ graph, selected, onSelect }: { graph: ResearchGraph; selected?: string; onSelect: (id?: string) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const positions = useRef<Record<string, XY>>({});
  const [flow, setFlow] = useState<ReactFlowInstance<ArgumentNode> | null>(null);
  const [hovered, setHovered] = useState<string>();
  const [layoutVersion, setLayoutVersion] = useState(0);
  const nodes = useMemo(() => {
    positions.current = stableLayout(graph, positions.current);
    const focus = hovered ?? selected;
    const focusedEdge = graph.edges.find(e => e.id === focus);
    const neighbours = new Set([focus, ...(focusedEdge ? [focusedEdge.source, focusedEdge.target] : []), ...graph.nodes.filter(n => focus && n.sourceIds.includes(focus)).map(n => n.id), ...graph.edges.flatMap(e => e.source === focus ? [e.target] : e.target === focus ? [e.source] : [])]);
    return graph.nodes.map(n => ({ id: n.id, type: 'argument' as const, position: positions.current[n.id], ...researchSize(n),
      selected: selected === n.id, data: { argument: n, dimmed: !!focus && !neighbours.has(n.id) }, ariaLabel: `${n.label}, ${n.state}` }));
  }, [graph, selected, hovered, layoutVersion]);
  const edges = useMemo(() => {
    let lane = 0;
    const top = Math.min(0, ...nodes.map(n => n.position.y)) - 64;
    return graph.edges.map(e => {
      const source = positions.current[e.source], target = positions.current[e.target];
      const above = e.relation === 'concerns' || (source && target && Math.abs(source.x - target.x) > 500 && e.relation !== 'blocks');
      const backwards = source && target && source.x > target.x;
      return { ...e, type: above ? 'above' : 'default', data: { routeY: above ? top - lane++ * 24 : undefined },
        sourceHandle: above ? 'above-out' : backwards ? 'back-out' : 'out', targetHandle: above ? 'above-in' : backwards ? 'back-in' : 'in', label: `${e.relation.replace(/_/g, ' ')}${e.auditVerdict === 'needs_review' ? ' · needs review' : e.auditVerdict === 'supported' ? ' · audited' : ''}`,
        style: { stroke: e.auditVerdict === 'needs_review' ? researchColor('warn') : e.relation === 'blocks' || e.relation === 'rebuts' || e.relation === 'undercuts' ? researchColor('fail') : '#a1a1aa', strokeWidth: 1, strokeDasharray: e.proposed ? '3 3' : undefined },
        labelStyle: { fill: '#85858e', fontSize: 9 }, labelBgStyle: { fill: '#fafaf9', fillOpacity: .96 }, labelBgPadding: [6, 3] as [number, number] };
    });
  }, [graph, nodes]);
  useEffect(() => { if (flow) void flow.fitView({ padding: .2, maxZoom: 1.1, duration: 650 }); }, [flow, !!selected, graph.nodes.length]);
  useEffect(() => {
    if (!flow || !container.current) return;
    let timer: ReturnType<typeof setTimeout>;
    const observer = new ResizeObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(() => void flow.fitView({ padding: .2, maxZoom: 1.1, duration: 250 }), 100);
    });
    observer.observe(container.current);
    return () => { observer.disconnect(); clearTimeout(timer); };
  }, [flow]);
  return <div ref={container} className={cn("absolute inset-0", selected && "min-[900px]:right-[350px]")} data-testid="research-canvas"><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onInit={setFlow}
    onNodeClick={(_, node) => onSelect(node.id)} onEdgeClick={(_, edge) => onSelect(!['grounds', 'premise_of', 'concludes', 'blocks'].includes(graph.edges.find(e => e.id === edge.id)?.relation ?? '') ? edge.id : edge.source)} onPaneClick={() => onSelect()} onNodeMouseEnter={(_, n) => setHovered(n.id)} onNodeMouseLeave={() => setHovered(undefined)}
    onNodesChange={changes => { let moved = false; for (const change of changes) if (change.type === 'position' && change.position) { const prior = positions.current[change.id]; if (!prior || prior.x !== change.position.x || prior.y !== change.position.y) { positions.current[change.id] = change.position; moved = true; } } if (moved) setLayoutVersion(v => v + 1); }}
    onNodeDragStop={(_, n) => { positions.current[n.id] = n.position; }} fitView minZoom={.15} maxZoom={2} nodesConnectable={false}>
    <Background variant={BackgroundVariant.Dots} gap={20} size={.65} color="#dedee2"/>
  </ReactFlow><Button variant="outline" size="sm" className="absolute bottom-5 left-5 text-[10px]" icon={<CornersOutIcon size={14}/>} onClick={() => void flow?.fitView({ padding: .2, maxZoom: 1.1 })}>Fit argument</Button></div>;
}
