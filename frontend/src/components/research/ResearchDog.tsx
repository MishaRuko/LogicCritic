/** A small dog sniffing for evidence, shown while the research agent works. */
export function ResearchDog({ size = 56 }: { size?: number }) {
  return <svg className="research-dog" width={size} height={size * 0.75} viewBox="0 0 64 48" fill="none" aria-hidden>
    <ellipse cx="32" cy="45" rx="20" ry="2" className="fill-zinc-200"/>
    <g className="research-dog-tail"><path d="M14 24 Q6 18 8 10" stroke="#a16207" strokeWidth="3" strokeLinecap="round"/></g>
    <rect x="12" y="20" width="30" height="14" rx="7" fill="#d97706"/>
    <rect x="15" y="31" width="4" height="12" rx="2" fill="#b45309"/>
    <rect x="22" y="31" width="4" height="12" rx="2" fill="#d97706"/>
    <rect x="31" y="31" width="4" height="12" rx="2" fill="#b45309"/>
    <rect x="37" y="31" width="4" height="12" rx="2" fill="#d97706"/>
    <g className="research-dog-head">
      <circle cx="46" cy="22" r="9" fill="#d97706"/>
      <ellipse cx="54" cy="26" rx="5" ry="4" fill="#f59e0b"/>
      <circle cx="58" cy="25" r="1.8" fill="#27272a"/>
      <circle cx="48" cy="19" r="1.4" fill="#27272a"/>
      <path d="M41 15 Q38 22 42 28 Q45 22 44 15 Z" fill="#92400e"/>
    </g>
  </svg>;
}
