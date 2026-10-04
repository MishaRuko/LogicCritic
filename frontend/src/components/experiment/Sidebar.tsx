import { useEffect, useState, type ReactElement, type ReactNode } from 'react';
import { Tooltip } from '@cloudflare/kumo';
import { CaretRightIcon, FlaskIcon, HouseIcon, InfoIcon, PlusIcon, SidebarSimpleIcon, TrashIcon, TreeStructureIcon } from '@phosphor-icons/react';
import { verifyRun } from '../../lib/experiment/conformance';
import { samples, time, type Sample } from '../../lib/experiment/demo';
import type { Analysis } from '../../lib/experiment/store';
import type { Status } from '../../lib/experiment/types';
import { StatusIcon } from './Status';

type Section = 'analyses' | 'samples';
const SECTIONS_KEY = 'lens-sidebar-sections';
const shortcut = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘B' : 'Ctrl+B';

function overall(a: Analysis): Status {
  const rr = Object.values(verifyRun(a.method, a.run));
  return rr.some(r => r.status === 'contradicted') ? 'contradicted' : rr.some(r => r.status === 'skipped') ? 'skipped' : rr.every(r => r.status === 'verified') ? 'verified' : 'unverifiable';
}

type Props = {
  collapsed: boolean; onCollapsedChange: (collapsed: boolean) => void;
  analyses: Analysis[]; sampleAnalyses: Analysis[];
  activeId?: string; activeSample?: Sample; howActive: boolean;
  onOpen: (a: Analysis) => void; onNew: (sample?: Sample) => void; onRemove: (id: string) => void;
  onHowItWorks: () => void;
};

export function Sidebar({ collapsed, onCollapsedChange, analyses, sampleAnalyses, activeId, activeSample, howActive, onOpen, onNew, onRemove, onHowItWorks }: Props) {
  const [closed, setClosed] = useState<Section[]>(() => { try { return JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? '[]'); } catch { return []; } });
  useEffect(() => { localStorage.setItem(SECTIONS_KEY, JSON.stringify(closed)); }, [closed]);
  const toggleSection = (s: Section) => setClosed(c => c.includes(s) ? c.filter(x => x !== s) : [...c, s]);

  // In the icon rail, labels move into tooltips and icons stand in for them; expanded items are text only.
  const tip = (content: ReactNode, trigger: ReactElement) => collapsed ? <Tooltip content={content} side="right" asChild>{trigger}</Tooltip> : trigger;

  const item = (key: string, active: boolean, onClick: () => void, icon: ReactNode, title: string, detail?: string, status?: Status, onDelete?: () => void) =>
    <div key={key} className={`nav-item ${active ? 'active' : ''}`}>
      {tip(<span className="rail-tip"><strong>{title}</strong>{detail && <small>{detail}</small>}</span>,
        <button onClick={onClick} aria-label={collapsed ? title : undefined} aria-current={active ? 'page' : undefined}>
          {collapsed && (status ? <StatusIcon status={status} size={14}/> : icon)}
          {!collapsed && <><div><span>{title}</span>{detail && <small>{detail}</small>}</div>{status && <StatusIcon status={status} size={12}/>}</>}
        </button>)}
      {!collapsed && onDelete && <button className="nav-remove" aria-label={`Delete ${title}`} onClick={onDelete}><TrashIcon size={13}/></button>}
    </div>;

  const analysisItem = (a: Analysis, removable: boolean) => item(a.id, activeId === a.id, () => onOpen(a), <FlaskIcon size={15}/>, a.method.title, `${a.videoName} · ${time(a.run.duration)}`, overall(a), removable ? () => onRemove(a.id) : undefined);

  const section = (id: Section, label: string, count: number, children: ReactNode) => {
    const open = collapsed || !closed.includes(id);
    return <div className="nav-section">
      {collapsed ? <div className="rail-divider"/> : <button className="nav-label" aria-expanded={open} onClick={() => toggleSection(id)}>
        <CaretRightIcon size={10} weight="bold" className={open ? 'open' : ''}/>{label}<span className="nav-count">{count}</span>
      </button>}
      {open && <nav>{children}</nav>}
    </div>;
  };

  return <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
    <div className="sidebar-head">
      {collapsed
        ? tip(`Expand sidebar · ${shortcut}`, <button className="sidebar-toggle expand" aria-label="Expand sidebar" onClick={() => onCollapsedChange(false)}><SidebarSimpleIcon size={18}/></button>)
        : <><span className="sidebar-brand"><strong>Trial</strong></span>
          <Tooltip content={`Collapse sidebar · ${shortcut}`} side="bottom" asChild><button className="sidebar-toggle" aria-label="Collapse sidebar" onClick={() => onCollapsedChange(true)}><SidebarSimpleIcon size={18}/></button></Tooltip></>}
    </div>

    {tip('New analysis', <button className="new-analysis-button" aria-label="New analysis" onClick={() => onNew()}><PlusIcon size={15} weight="bold"/>{!collapsed && <span>New analysis</span>}</button>)}

    <div className="sidebar-scroll">
      {analyses.length > 0 && section('analyses', 'Your analyses', analyses.length, [...analyses].reverse().map(a => analysisItem(a, true)))}
      {section('samples', 'Samples', samples.length, samples.map(s => {
        const a = sampleAnalyses.find(a => a.run.id === s.id);
        return a ? analysisItem(a, false) : item(s.id, activeSample === s, () => onNew(s), <FlaskIcon size={15}/>, s.title, `${s.id} · ${time(s.duration)} · not analysed`);
      }))}
    </div>

    <div className="sidebar-bottom">
      {item('home', false, () => location.assign('/'), <HouseIcon size={15}/>, 'Home')}
      {item('argument', false, () => location.assign('/research'), <TreeStructureIcon size={15}/>, 'Argument check')}
      {item('how', howActive, onHowItWorks, <InfoIcon size={15}/>, 'How it works')}
    </div>
  </aside>;
}
