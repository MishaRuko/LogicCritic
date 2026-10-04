import { useEffect, useRef, useState } from 'react';
import { Banner, Button, Loader } from '@cloudflare/kumo';
import { ArrowClockwiseIcon, ArrowRightIcon, CheckIcon, FileTextIcon, FilmStripIcon, UploadSimpleIcon, WarningIcon, XIcon } from '@phosphor-icons/react';
import { clock, runAgent, type AgentEvent, type Frame } from '../../lib/experiment/agent';
import { cachedAnalysis, samples, time, type Sample } from '../../lib/experiment/demo';
import { grabFrames, openVideo, overviewCount, spacedTimes } from '../../lib/experiment/frames';
import { putVideo, type Analysis } from '../../lib/experiment/store';
import type { MethodContract } from '../../lib/experiment/types';

type MethodInput = { name: string; file?: File; sample?: Sample };
type VideoInput = { name: string; url: string; duration: number; file?: File; sample?: Sample };
type Stage = 'method' | 'frames' | 'agent' | 'done';
type LogLine = { id: number; kind: AgentEvent['kind']; text: string };

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const json = await response.json().catch(() => ({ error: `Server returned ${response.status}. Is the LogicCritic frontend server running?` }));
  if (!response.ok) throw new Error(json.error ?? `Request failed (${response.status}).`);
  return json as T;
}
const base64 = (file: Blob) => new Promise<string>((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result).split(',')[1]); r.onerror = () => reject(r.error); r.readAsDataURL(file); });
async function methodDocument(input: MethodInput) {
  if (input.sample) return { kind: 'pdf' as const, data: await base64(await (await fetch(input.sample.protocolPdf)).blob()) };
  const file = input.file!;
  if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) return { kind: 'pdf' as const, data: await base64(file) };
  return { kind: 'text' as const, text: await file.text() };
}
function videoDuration(url: string) {
  return new Promise<number>((resolve, reject) => { const v = document.createElement('video'); v.preload = 'metadata'; v.onloadedmetadata = () => resolve(v.duration); v.onerror = () => reject(new Error('This browser cannot read that video. Try an H.264 MP4.')); v.src = url; });
}

export function NewAnalysis({ initialSample, claudeReady, onComplete, onOpen }: { initialSample?: Sample; claudeReady: boolean | undefined; onComplete: (analysis: Analysis) => void; onOpen: (analysis: Analysis) => void }) {
  const [method, setMethod] = useState<MethodInput>();
  const [video, setVideo] = useState<VideoInput>();
  const [stage, setStage] = useState<Stage>();
  const [error, setError] = useState('');
  const [extracted, setExtracted] = useState<MethodContract>();
  const [overview, setOverview] = useState<Frame[]>([]);
  const [log, setLog] = useState<LogLine[]>([]);
  const [inspecting, setInspecting] = useState<{ start: number; end: number }>();
  const logEnd = useRef<HTMLDivElement>(null);
  useEffect(() => { if (initialSample) chooseSample(initialSample); }, [initialSample]);
  useEffect(() => { logEnd.current?.scrollIntoView({ block: 'nearest' }); }, [log]);
  function chooseSample(sample: Sample) {
    setMethod({ name: sample.protocolName, sample }); setVideo({ name: `${sample.id}.mp4`, url: sample.video, duration: sample.duration, sample }); reset();
  }
  function reset() { setStage(undefined); setError(''); setExtracted(undefined); setOverview([]); setLog([]); setInspecting(undefined); }
  async function chooseVideo(file: File) {
    const url = URL.createObjectURL(file);
    try { setVideo({ name: file.name, url, duration: await videoDuration(url), file }); reset(); } catch (e) { URL.revokeObjectURL(url); setError((e as Error).message); }
  }

  async function run() {
    if (!method || !video) return;
    reset(); let line = 0;
    const push = (kind: AgentEvent['kind'], text: string) => setLog(l => [...l, { id: line++, kind, text }]);
    try {
      setStage('method');
      const { method: contract } = await post<{ method: MethodContract }>('/experiment-api/method', { name: method.name, document: await methodDocument(method) });
      setExtracted(contract);
      setStage('frames');
      const element = await openVideo(video.url);
      const frames = await grabFrames(element, spacedTimes(0, video.duration, overviewCount(video.duration)), 512);
      setOverview(frames);
      setStage('agent');
      const lines: string[] = [];
      const { observations, absences } = await runAgent({
        method: contract, duration: video.duration, overview: frames,
        getFrames: (start, end, count) => grabFrames(element, spacedTimes(start, end, count), 768),
        call: messages => post('/experiment-api/agent-turn', { messages }),
        onEvent: e => {
          const text = e.kind === 'overview' ? `Reviewing ${e.frames} overview frames across ${clock(e.duration)}` : e.kind === 'inspect' ? `Looking closely at ${clock(e.start)}–${clock(e.end)} · ${e.count} frames` : e.kind === 'thinking' ? e.text : e.kind === 'retry' ? e.message : `Submitted findings for ${e.steps} steps`;
          if (e.kind === 'inspect') setInspecting({ start: e.start, end: e.end });
          lines.push(text); push(e.kind, text);
        }
      });
      setStage('done'); setInspecting(undefined);
      const id = crypto.randomUUID();
      if (video.file) await putVideo(id, video.file);
      onComplete({
        id, createdAt: new Date().toISOString(), methodName: method.name, videoName: video.name, method: contract, mode: 'agent', log: lines, videoStored: !!video.file,
        run: { ...(video.sample?.run ?? { id, title: contract.title, subtitle: video.name, date: new Date().toISOString().slice(0, 10), poster: undefined }), id, title: contract.title, video: video.file ? `idb:${id}` : video.url, duration: video.duration, observations, absences }
      });
    } catch (e) { setError((e as Error).message); setInspecting(undefined); }
  }

  const busy = !!stage && stage !== 'done' && !error;
  const cached = method?.sample && method.sample === video?.sample ? cachedAnalysis(method.sample) : undefined;
  if (stage) return <div className="new-analysis running">
    <div className="page-intro"><h1>Analysing</h1><p>{method?.name} · {video?.name}</p></div>
    <ol className="pipeline">
      <PipelineStep state={state('method', stage, error)} title="Read the methodology" detail={extracted ? `${extracted.requirements.length} steps extracted from ${method?.name}` : `Claude is reading ${method?.name}`}>
        {extracted && <ol className="extracted-steps">{extracted.requirements.map(r => <li key={r.id}><span className="mono">{r.order.toString().padStart(2, '0')}</span><div><strong>{r.title}</strong><span>{r.checks?.length ?? 0} checks{r.caveats?.length ? ` · ${r.caveats.length} not visible on video` : ''}</span></div></li>)}</ol>}
      </PipelineStep>
      <PipelineStep state={state('frames', stage, error)} title="Sample the recording" detail={overview.length ? `${overview.length} frames across ${time(video!.duration)}` : 'Extracting overview frames in the browser'}>
        {overview.length > 0 && <div className="filmstrip">{overview.map(f => <img key={f.t} src={`data:image/jpeg;base64,${f.data}`} alt={`Frame at ${time(f.t)}`} className={inspecting && f.t >= inspecting.start - 1 && f.t <= inspecting.end + 1 ? 'inspecting' : ''}/>)}</div>}
      </PipelineStep>
      <PipelineStep state={state('agent', stage, error)} title="Agent inspects each step" detail="Claude requests close-ups of the windows it needs, then reports per-step findings">
        {log.length > 0 && <div className="agent-log">{log.map(l => <p key={l.id} className={l.kind}>{l.kind === 'thinking' ? l.text.split('\n')[0].slice(0, 220) : l.text}</p>)}<div ref={logEnd}/></div>}
      </PipelineStep>
    </ol>
    {error && <Banner className="pipeline-error" variant="error" icon={<WarningIcon weight="fill"/>} title="Analysis stopped" description={error} action={<div className="banner-actions"><Button size="sm" variant="secondary" icon={ArrowClockwiseIcon} onClick={() => void run()}>Retry</Button><Button size="sm" variant="ghost" onClick={reset}>Back</Button></div>}/>}
    {busy && <p className="muted small">Typically 1–3 minutes. Keep this tab open.</p>}
  </div>;

  return <div className="new-analysis">
    <div className="page-intro"><h1>New analysis</h1><p>Upload the methodology and a recording of it being carried out. Claude extracts the steps, then an agent watches the video and checks each one.</p></div>
    <div className="dropzones">
      <Dropzone icon={<FileTextIcon size={20}/>} label="Methodology" hint="Paper or protocol · PDF, TXT, MD" accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown" value={method?.name} onFile={file => { setMethod({ name: file.name, file }); reset(); }} onClear={() => setMethod(undefined)}/>
      <Dropzone icon={<FilmStripIcon size={20}/>} label="Recording" hint="Video of the bench · MP4, MOV, WebM" accept="video/*" value={video ? `${video.name} · ${time(video.duration)}` : undefined} onFile={file => void chooseVideo(file)} onClear={() => setVideo(undefined)}/>
    </div>
    {error && <Banner className="form-error" variant="error" icon={<WarningIcon weight="fill"/>} description={error}/>}
    <div className="run-row">
      <Button variant="secondary" size="sm" icon={ArrowRightIcon} disabled={!method || !video || claudeReady === false} onClick={() => void run()}>Extract and analyse</Button>
      {cached && <Button variant="ghost" size="sm" onClick={() => onOpen(cached)}>Open cached result</Button>}
      {claudeReady === false && <span className="muted small">Set <code>CLAUDE_API_KEY</code> in <code>LogicCritic/.env</code> and restart the frontend.</span>}
    </div>
    <section className="samples">
      <div className="section-heading"><h2>Samples</h2><span>Real wet-lab recordings from LabSuperVision, with published step timings and error labels for checking the agent.</span></div>
      {samples.map(s => <button key={s.id} className={`sample-row ${method?.sample === s && video?.sample === s ? 'selected' : ''}`} onClick={() => chooseSample(s)}>
        <img src={s.poster} alt=""/><div><strong>{s.title}</strong><span>{s.deviation}</span></div><span className="mono">{time(s.duration)}</span><span className="mono muted">{s.id}</span>
      </button>)}
    </section>
  </div>;
}

function state(step: Stage, current: Stage, error: string): 'done' | 'active' | 'waiting' | 'failed' {
  const order: Stage[] = ['method', 'frames', 'agent', 'done'];
  const a = order.indexOf(step), b = order.indexOf(current);
  return a < b ? 'done' : a === b ? (error ? 'failed' : 'active') : 'waiting';
}
function PipelineStep({ state, title, detail, children }: { state: 'done' | 'active' | 'waiting' | 'failed'; title: string; detail: string; children?: React.ReactNode }) {
  return <li className={`pipeline-step ${state}`}>
    <span className="pipeline-icon">{state === 'done' ? <CheckIcon size={14} weight="bold"/> : state === 'active' ? <Loader size="sm"/> : state === 'failed' ? <WarningIcon size={14} weight="bold"/> : <span className="dash"/>}</span>
    <div><strong>{title}</strong><span>{detail}</span>{children}</div>
  </li>;
}
function Dropzone({ icon, label, hint, accept, value, onFile, onClear }: { icon: React.ReactNode; label: string; hint: string; accept: string; value?: string; onFile: (file: File) => void; onClear: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return <div className={`dropzone ${value ? 'filled' : ''} ${over ? 'over' : ''}`} onClick={() => input.current?.click()}
    onDragOver={e => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
    onDrop={e => { e.preventDefault(); setOver(false); const file = e.dataTransfer.files[0]; if (file) onFile(file); }}>
    <input ref={input} type="file" accept={accept} hidden onChange={e => { const file = e.target.files?.[0]; if (file) onFile(file); e.target.value = ''; }}/>
    {icon}<span className="label">{label}</span>
    {value ? <strong>{value}</strong> : <strong className="muted"><UploadSimpleIcon size={13}/>Drop a file or click to browse</strong>}
    <span className="hint">{hint}</span>
    {value && <button className="clear" aria-label={`Remove ${label.toLowerCase()}`} onClick={e => { e.stopPropagation(); onClear(); }}><XIcon size={13}/></button>}
  </div>;
}
