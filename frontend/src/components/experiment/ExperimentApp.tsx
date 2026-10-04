'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Collapsible, Empty, Tabs, Toasty } from '@cloudflare/kumo';
import { MotionConfig } from 'motion/react';
import './experiment.css';
import { ArrowClockwiseIcon, DownloadSimpleIcon, FileTextIcon } from '@phosphor-icons/react';
import { notify, toasts } from '../../lib/experiment/toast';
import { cachedAnalysis, samples, time, type Sample } from '../../lib/experiment/demo';
import { verifyRun } from '../../lib/experiment/conformance';
import { createRecord, keyMoment } from '../../lib/experiment/record';
import { grabFrame, openVideo } from '../../lib/experiment/frames';
import type { RecordSnapshot } from '../../lib/experiment/types';
import { observationAt } from '../../lib/experiment/playback';
import { deleteVideo, getVideo, loadAnalyses, saveAnalyses, type Analysis } from '../../lib/experiment/store';
import { linkAnalysis, methodFromSource, readHandoff, workspaceUrl } from '../../lib/experiment/handoff';
import type { ExperimentRecord } from '../../lib/experiment/types';
import { ExecutionWorkspace } from './ExecutionWorkspace';
import { HowItWorks } from './HowItWorks';
import { NewAnalysis } from './NewAnalysis';
import { RecordView, downloadJSON } from './RecordView';
import { Sidebar } from './Sidebar';
import { SharedRecord } from './SharedRecord';

type Page = { kind: 'new'; sample?: Sample; method?: File } | { kind: 'analysis'; id: string } | { kind: 'how' };
type Tab = 'execution' | 'method' | 'record';
const sampleAnalyses = samples.map(cachedAnalysis).filter((a): a is Analysis => !!a);

/** Trial's app (lab-experiment-check e9bb2f1), mounted at /experiment. */
export function ExperimentApp() {
  return <MotionConfig reducedMotion="user"><Toasty toastManager={toasts}><div className="experiment-root"><App/></div></Toasty></MotionConfig>;
}

/** A published record at /experiment/r/<id>, with the same styles and providers. */
export function SharedRecordPage({ id }: { id: string }) {
  return <MotionConfig reducedMotion="user"><Toasty toastManager={toasts}><div className="experiment-root"><SharedRecord id={id}/></div></Toasty></MotionConfig>;
}

function App() {
  const [analyses, setAnalyses] = useState<Analysis[]>(loadAnalyses);
  const [page, setPage] = useState<Page>(() => {
    // ?analysis=<id> opens one analysis (linked from an argument workspace).
    const requested = new URLSearchParams(window.location.search).get('analysis');
    if (requested && [...loadAnalyses(), ...sampleAnalyses].some(a => a.id === requested)) return { kind: 'analysis', id: requested };
    return sampleAnalyses[0] ? { kind: 'analysis', id: sampleAnalyses.find(a => a.run.id === 'DJI_16')?.id ?? sampleAnalyses[0].id } : { kind: 'new' };
  });
  const [tab, setTab] = useState<Tab>('execution');
  const [selected, setSelected] = useState('');
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [videoUrl, setVideoUrl] = useState<string>();
  const [records, setRecords] = useState<Record<string, ExperimentRecord>>({});
  const [claudeReady, setClaudeReady] = useState<boolean>();
  const [collapsed, setCollapsed] = useState(() => { const stored = localStorage.getItem('lens-sidebar-collapsed'); return stored ? stored === 'true' : window.innerWidth < 1100; });
  const video = useRef<HTMLVideoElement>(null);
  const [handoff] = useState(() => readHandoff(window.location.search));

  const analysis = page.kind === 'analysis' ? [...analyses, ...sampleAnalyses].find(a => a.id === page.id) : undefined;
  const run = useMemo(() => analysis && videoUrl ? { ...analysis.run, video: videoUrl } : undefined, [analysis, videoUrl]);
  const method = analysis?.method;
  const results = useMemo(() => analysis ? verifyRun(analysis.method, analysis.run) : {}, [analysis]);

  useEffect(() => { if (analysis && !handoff) open(analysis); }, []);
  // Continue to experiment: start a new analysis with the workspace source as the method.
  useEffect(() => {
    if (!handoff) return;
    setPage({ kind: 'new' });
    methodFromSource(handoff.source).then(method => setPage({ kind: 'new', method }), e => notify(e instanceof Error ? e.message : String(e)));
  }, [handoff]);
  useEffect(() => { saveAnalyses(analyses); }, [analyses]);
  useEffect(() => { localStorage.setItem('lens-sidebar-collapsed', String(collapsed)); }, [collapsed]);
  useEffect(() => {
    const toggle = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'b') { e.preventDefault(); setCollapsed(c => !c); } };
    window.addEventListener('keydown', toggle); return () => window.removeEventListener('keydown', toggle);
  }, []);
  useEffect(() => { fetch('/experiment-api/health').then(r => r.json()).then(h => setClaudeReady(!!h.claudeConfigured)).catch(() => setClaudeReady(false)); }, []);
  // Resolve the recording: samples are static files, uploads live in IndexedDB.
  useEffect(() => {
    setVideoUrl(undefined);
    if (!analysis) return;
    if (!analysis.run.video.startsWith('idb:')) { setVideoUrl(analysis.run.video); return; }
    let url: string | undefined, cancelled = false;
    getVideo(analysis.id).then(blob => { if (cancelled) return; if (!blob) { notify('The uploaded recording is no longer stored in this browser.'); return; } url = URL.createObjectURL(blob); setVideoUrl(url); });
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [analysis?.id]);

  function open(next: Analysis) {
    setPage({ kind: 'analysis', id: next.id }); setTab('execution'); setPlaying(false);
    const first = [...next.run.observations].sort((a, b) => a.timestampStart - b.timestampStart)[0];
    const results = verifyRun(next.method, next.run), flaggedAs = (s: string) => next.method.requirements.find(r => results[r.id]?.status === s);
    const flagged = flaggedAs('contradicted') ?? flaggedAs('skipped'), gap = flagged && results[flagged.id];
    const focus = flagged ? next.run.observations.find(o => o.stepId === flagged.id) : first;
    setSelected(flagged?.id ?? focus?.stepId ?? next.method.requirements[0].id);
    setCurrentTime(focus ? focus.evidence[0]?.timestamp ?? focus.timestampStart : gap?.status === 'skipped' ? gap.between[0] : 0);
  }
  const seek = useCallback((seconds: number) => { const v = video.current; if (v) v.currentTime = Math.min(seconds, analysis?.run.duration ?? seconds); setCurrentTime(seconds); }, [analysis?.run.duration]);
  const selectStep = useCallback((id: string) => {
    video.current?.pause(); setSelected(id);
    const o = analysis?.run.observations.find(o => o.stepId === id);
    if (o) seek(o.evidence.find(e => e.kind === 'video')?.timestamp ?? o.timestampStart);
  }, [analysis, seek]);
  async function generate() {
    if (!analysis || !run) return;
    let snapshot: RecordSnapshot | undefined;
    try {
      const moment = keyMoment(analysis.run, analysis.method);
      const frame = await grabFrame(await openVideo(run.video), moment.t, 960);
      snapshot = { ...moment, t: frame.t, image: `data:image/jpeg;base64,${frame.data}` };
    } catch { /* The record is still valid without a still. */ }
    const record = await createRecord({ ...analysis.run }, analysis.method, snapshot);
    setRecords(r => ({ ...r, [analysis.id]: record })); setTab('record');
  }
  function remove(id: string) {
    setAnalyses(a => a.filter(x => x.id !== id)); void deleteVideo(id);
    if (page.kind === 'analysis' && page.id === id) setPage({ kind: 'new' });
  }
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!method || page.kind !== 'analysis' || tab !== 'execution' || e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); const i = method.requirements.findIndex(r => r.id === selected); selectStep(method.requirements[Math.max(0, Math.min(method.requirements.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))].id); }
      if (e.key === ' ') { e.preventDefault(); if (playing) video.current?.pause(); else void video.current?.play(); }
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  });

  return <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>
    <Sidebar collapsed={collapsed} onCollapsedChange={setCollapsed} analyses={analyses} sampleAnalyses={sampleAnalyses}
      activeId={page.kind === 'analysis' ? page.id : undefined} activeSample={page.kind === 'new' ? page.sample : undefined} howActive={page.kind === 'how'}
      onOpen={open} onNew={sample => setPage({ kind: 'new', sample })} onRemove={remove} onHowItWorks={() => setPage({ kind: 'how' })}/>
    <div className="app-main"><div className="page">
      {handoff && <div className="handoff-banner"><span>Method from your argument workspace. Results are linked back to it.</span><a href={workspaceUrl(handoff.workspace)}>← Back to workspace</a></div>}
      {page.kind === 'new' ? <NewAnalysis key={page.sample?.id ?? page.method?.name ?? 'blank'} initialSample={page.sample} initialMethod={page.method} claudeReady={claudeReady} onOpen={open} onComplete={a => { setAnalyses(list => [...list, a]); if (handoff) linkAnalysis(handoff.workspace, a.id); open(a); }}/>
      : page.kind === 'how' ? <HowItWorks onExample={() => { const a = sampleAnalyses.find(a => a.run.id === 'DJI_16'); if (a) open(a); else setPage({ kind: 'new', sample: samples.find(s => s.id === 'DJI_16') }); }}/>
      : analysis && method ? <>
        <div className="experiment-heading">
          <div><h1>{method.title}</h1><p>{analysis.videoName} <span className="sep">·</span> {time(analysis.run.duration)} recording <span className="sep">·</span> {analysis.mode === 'agent' ? 'Claude agent' : 'Dataset annotation-assisted'}</p></div>
          <div className="experiment-actions">{analysis.id.startsWith('sample-') && <Button variant="ghost" size="sm" icon={ArrowClockwiseIcon} onClick={() => setPage({ kind: 'new', sample: samples.find(s => s.id === analysis.run.id) })}>Re-run agent</Button>}<Button variant="secondary" size="sm" icon={FileTextIcon} onClick={() => void generate()}>Generate record</Button></div>
        </div>
        <div className="tabbar"><Tabs size="sm" variant="underline" indicatorClassName="tab-indicator" value={tab} onValueChange={v => setTab(v as Tab)} tabs={[{ value: 'execution', label: 'Execution' }, { value: 'method', label: 'Method' }, { value: 'record', label: 'Record' }]}/></div>
        {tab === 'execution' ? run ? <ExecutionWorkspace run={run} method={method} results={results} selected={selected || method.requirements[0].id} currentTime={currentTime} playing={playing} videoRef={video}
            sourceLabel={analysis.mode === 'agent' ? 'Observed by Claude agent' : 'Observations from dataset annotations'}
            onSelect={selectStep} onSeek={seconds => { seek(seconds); const active = observationAt(analysis.run.observations, seconds); if (active) setSelected(active.stepId); }}
            onTimeUpdate={seconds => { setCurrentTime(seconds); if (playing) { const active = observationAt(analysis.run.observations, seconds); if (active) setSelected(active.stepId); } }}
            onPlaying={setPlaying} onMethod={() => setTab('method')} onNotice={notify}/> : <p className="muted">Loading recording…</p>
        : tab === 'method' ? <div className="method-page">
            <div className="section-heading"><div><h2>Extracted methodology</h2><span>From {analysis.methodName}{method.summary ? ` · ${method.summary}` : ''}</span></div><Button size="sm" variant="ghost" icon={DownloadSimpleIcon} onClick={() => downloadJSON(method, 'lens-method.json')}>Export</Button></div>
            <ol className="method-list">{method.requirements.map(r => <li key={r.id}>
              <span className="mono">{r.order.toString().padStart(2, '0')}</span>
              <div><strong>{r.title}</strong><p>{r.description}</p>
                {r.checks?.length ? <ul className="checks">{r.checks.map(c => <li key={c}>{c}</li>)}</ul> : null}
                {r.caveats?.length ? <p className="caveats">Not establishable from video: {r.caveats.join(' · ')}</p> : null}
                {!r.checks && <p className="caveats">{[r.action, r.target && `target: ${r.target}`, r.duration && `duration: ${r.duration.expectedSeconds}s`].filter(Boolean).join(' · ')}</p>}
              </div>
              <span className={`criticality ${r.criticality}`}>{r.criticality}</span>
            </li>)}</ol>
            {analysis.log?.length ? <Collapsible.Root className="agent-trace"><Collapsible.DefaultTrigger>Agent trace · {analysis.log.length} entries</Collapsible.DefaultTrigger><Collapsible.DefaultPanel>{analysis.log.map((l, i) => <p key={i}>{l}</p>)}</Collapsible.DefaultPanel></Collapsible.Root> : null}
          </div>
        : records[analysis.id] ? <RecordView record={records[analysis.id]} onStep={id => { setTab('execution'); selectStep(id); }}/>
        : <Empty icon={<FileTextIcon size={28}/>} title="Experiment record" description="A hashed record of the methodology, the agent’s observations, deviations and everything the recording could not establish." contents={<Button variant="secondary" size="sm" icon={FileTextIcon} onClick={() => void generate()}>Generate record</Button>}/>}
      </> : <Empty title="Analysis not found" contents={<Button size="sm" variant="secondary" onClick={() => setPage({ kind: 'new' })}>New analysis</Button>}/>}
    </div></div>
  </div>;
}
