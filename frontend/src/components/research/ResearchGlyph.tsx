import type { ResearchKind, ResearchState } from '../../lib/research/graph';
const stateColor = { unknown: '#85858e', pass: '#4d7963', warn: '#a07832', fail: '#b34d4d' };
const ink = '#27272a',
  paper = '#fafaf9';
export const researchColor = (state: ResearchState = 'idle') =>
  state === 'idle' ? ink : stateColor[state];
const hexagon = Array.from({ length: 6 }, (_, i) => {
  const a = (Math.PI / 180) * (60 * i - 90);
  return `${(9.5 * Math.cos(a)).toFixed(2)},${(9.5 * Math.sin(a)).toFixed(2)}`;
}).join(' ');
/** Shape encodes the argument object; colour encodes its derived state; a dashed outline marks proposed, unreviewed objects. */
export function ResearchGlyph({
  kind,
  state = 'idle',
  proposed = false,
  assumption = false,
  size = 22,
}: {
  kind: ResearchKind;
  state?: ResearchState;
  proposed?: boolean;
  assumption?: boolean;
  size?: number;
}) {
  const color = researchColor(state);
  const dash = proposed || assumption ? '2.2 2' : undefined;
  const line = {
    fill: paper,
    stroke: color,
    strokeWidth: state === 'fail' ? 1.6 : 1.2,
    strokeDasharray: dash,
  };
  const shape =
    kind === 'conclusion' ? (
      <>
        <circle r={10.5} fill="none" stroke={color} strokeWidth={1} strokeDasharray={dash} />
        <circle
          r={6.5}
          fill={proposed ? paper : color}
          stroke={color}
          strokeWidth={proposed ? 1.2 : 0}
        />
      </>
    ) : kind === 'statement' ? (
      <>
        <circle r={8} {...line} />
        <circle r={2.4} fill={color} />
      </>
    ) : kind === 'step' ? (
      <path d="M0 -9.5L9.5 0L0 9.5L-9.5 0Z" {...line} />
    ) : kind === 'excerpt' ? (
      <>
        <rect x={-7} y={-7} width={14} height={14} {...line} />
        <path d="M-3.5 -2.5h7M-3.5 .5h7M-3.5 3.5h4" stroke={color} strokeWidth={1} />
      </>
    ) : kind === 'obligation' ? (
      <>
        <polygon points={hexagon} {...line} />
        <path d="M0 -4.5v5M0 3v1.2" stroke={color} strokeWidth={1.4} />
      </>
    ) : (
      <>
        <circle r={10.5} fill="none" stroke={color} strokeWidth={1.2} />
        <circle r={6} fill="none" stroke={color} strokeWidth={1.2} />
      </>
    );
  return (
    <svg
      width={size}
      height={size}
      viewBox="-12 -12 24 24"
      aria-hidden
      className="overflow-visible"
    >
      {shape}
    </svg>
  );
}
