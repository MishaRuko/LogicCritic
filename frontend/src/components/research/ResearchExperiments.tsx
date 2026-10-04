'use client';

import { useRef, useState } from 'react';
import { Button, cn } from '@cloudflare/kumo';
import { ArrowRightIcon, CheckIcon, PlayIcon } from '@phosphor-icons/react';
import * as api from '../../lib/research/api';
import { isLabSample, labSample } from '../../lib/research/demo';
import { humanize } from '../../lib/research/graph';
import type { Experiments, Snapshot } from '../../types/api';
import { ExperimentDetail } from '../experiment/ExperimentDetail';
import { ExperimentAnalysisDock } from '../experiment/ExperimentAnalysisDock';
import type { Perform } from './ResearchPanels';

export function ResearchStages({ stage, hasResearch, verified, onChange }: { stage: number; hasResearch: boolean; verified: boolean; onChange: (stage: number) => void }) {
  return <nav aria-label="Research workflow" className="research-stages flex items-center justify-center gap-3">
    {['Add research', 'Verify research', 'Run experiment'].map((label, index) => <div key={label} className="research-stage flex items-center gap-3">
      {index > 0 && <ArrowRightIcon size={14} className="text-zinc-300" aria-hidden/>}
      <button aria-current={stage === index ? 'step' : undefined} disabled={index > 0 && !hasResearch} onClick={() => onChange(index)} className={cn('flex items-center gap-2 rounded-md px-2 py-1.5 text-[11px] transition-colors', stage === index ? index === 2 ? 'bg-pass/5 text-pass' : 'bg-zinc-100 text-ink' : 'text-zinc-400 hover:text-ink', index > 0 && !hasResearch && 'opacity-50')}>
        <span className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]', stage === index ? 'border-zinc-600 bg-zinc-700 text-white' : 'border-line', (index === 0 && hasResearch || index === 1 && verified) && 'border-pass/20 bg-pass/10 text-pass')}>
          {index === 0 && hasResearch || index === 1 && verified ? <CheckIcon size={12}/> : index + 1}
        </span><span>{label}</span>
      </button>
    </div>)}
  </nav>;
}

export function ResearchExperiments({ state, data, loading, error, busy, perform, onVerify, onSource }: {
  state: Snapshot; data?: Experiments; loading: boolean; error: Error | null; busy: boolean;
  perform: Perform; onVerify: () => void; onSource: (id: string) => void;
}) {
  const sources = state.sources.filter(source => source.origin !== 'lab-vision');
  const [sourceId, setSourceId] = useState('');
  const [runId, setRunId] = useState('');
  const [file, setFile] = useState<File>();
  const [partial, setPartial] = useState(false);
  const [setup, setSetup] = useState(false);
  const [starting, setStarting] = useState(false);
  const startVersion = useRef(0);
  const selectedSource = sources.find(source => source.id === sourceId) ?? sources[0];
  const sample = !!selectedSource && isLabSample(selectedSource.original_filename);
  const protocol = data?.protocols.find(p => p.source_id === selectedSource?.id && p.current);
  const run = data?.runs.find(r => r.id === runId) ?? data?.runs[0];
  const runProtocol = data?.protocols.find(p => p.id === run?.protocol_id);
  const processing = state.jobs.some(job => ['queued', 'running'].includes(job.status));
  const ready = !!data?.verified && !processing && !!selectedSource;
  const activeVideo = data?.runs.some(r => r.mode === 'video' && ['queued', 'running'].includes(r.status));
  const activeReplay = data?.runs.some(r => r.mode !== 'video' && ['queued', 'running'].includes(r.status));
  const completedDemo = data?.runs.find(item => item.status === 'succeeded' && (item.mode === 'video' || item.filename === 'DJI_08-first-30s.observations.jsonl') && data.protocols.find(draft => draft.id === item.protocol_id)?.source_id === selectedSource?.id);
  const canSkipDemo = (starting || !!run && ['queued', 'running'].includes(run.status)) && (!!completedDemo || sample && ready && !busy && !activeReplay);
  async function start(mode: 'demo' | 'video' | 'replay', sampleRun = false) {
    const version = ++startVersion.current;
    setStarting(true);
    try {
      let recording = file;
      if (sampleRun) {
        const live = mode === 'video';
        const response = await fetch(live ? labSample.video : labSample.observations);
        if (!response.ok) throw new Error('The sample recording or saved analysis could not be loaded.');
        recording = new File([await response.blob()], live ? 'DJI_08-first-30s.mp4' : 'DJI_08-first-30s.observations.jsonl', { type: live ? 'video/mp4' : 'application/x-ndjson' });
      }
      const created = await api.startExperiment(state.workspace.id, protocol?.id, mode, recording, selectedSource!.id, sampleRun || partial);
      if (version === startVersion.current) { setRunId(created.id); setSetup(false); }
    } finally { if (version === startVersion.current) setStarting(false); }
  }
  function skipToDemo() {
    if (completedDemo) {
      ++startVersion.current; setStarting(false); setRunId(completedDemo.id); setSetup(false);
    } else {
      void perform(() => start('replay', true));
    }
  }
  const actions = <>{sample && <><Button size="sm" disabled={busy || !ready || activeVideo} onClick={() => perform(() => start('video', true))}><PlayIcon size={14} className="mr-2"/>Analyse sample live</Button><Button size="sm" variant="ghost" disabled={busy || !ready || activeReplay} onClick={() => perform(() => start('replay', true))}>{run ? 'Run sample again' : 'Run sample analysis'}</Button></>}{run && <Button size="sm" variant="ghost" onClick={() => setSetup(value => !value)}>{setup ? 'Close upload' : 'Change recording'}</Button>}</>;
  return <div className="experiment-app experiment-layout h-full bg-paper"><div className="experiment-scroll px-5 py-7 sm:px-9"><div className="mx-auto max-w-[1440px]">
    {!(run?.status === 'succeeded' && runProtocol) && (sample || run) && <div className="mb-6 flex flex-wrap justify-end gap-3">{actions}</div>}
    {loading && <p role="status" className="muted mb-5">Loading experiments…</p>}
    {error && <p role="alert" className="text-fail mb-5">{error.message}</p>}
    {starting && <p role="status" className="text-running mb-5">Extracting source-grounded methodology and preparing the recording…</p>}
    {data && !data.verified && <div className="experiment-notice flex items-center justify-between gap-3"><p>{processing ? 'Research is still processing. Verify it once it finishes.' : 'Verify the current research to run an experiment.'}</p><Button size="sm" variant="ghost" onClick={onVerify}>Go to verification</Button></div>}
    {(!run || setup) && <section aria-label="Start experiment" className="mb-7">
      {sources.length > 1 && <div className="setup-row"><label htmlFor="methodology-source">Research source</label><select id="methodology-source" className="min-w-0 flex-1 bg-transparent py-2 text-xs" value={selectedSource?.id ?? ''} onChange={event => setSourceId(event.target.value)}>{sources.map(source => <option key={source.id} value={source.id}>{source.title || source.original_filename}</option>)}</select></div>}
      {sample && !run && <div className="grid gap-8 py-6 min-[1000px]:grid-cols-[minmax(0,1fr)_310px]"><video controls preload="metadata" src={labSample.video} poster="/demo/lsv/DJI_08-preview.jpg" aria-label="30-second sample recording" className="w-full max-h-[50vh] bg-zinc-900 object-contain"/><div><h2>DJI_08 · first 30 seconds</h2><p className="muted mt-3!">Cell preparation, paired with its original protocol.</p><p className="muted mt-3!">Analyse sample live to watch the methodology agent identify checks, the recording split into frames, and the video agent inspect the evidence. Live analysis uses model tokens.</p><p className="muted mt-3!">Run sample analysis replays saved observations for results in a few seconds, without new model calls.</p></div></div>}
      <div className="setup-row"><div><label htmlFor="experiment-recording" className="block mb-2 text-xs">{sample ? 'Or upload your recording' : 'Choose a lab video or saved observations'}</label><input id="experiment-recording" type="file" accept=".mp4,.mov,.webm,.avi,.m4v,.jsonl" disabled={busy} onChange={event => { const next = event.target.files?.[0]; setFile(next); setPartial(!!next?.name.includes('first-30s')); }} className="w-full text-[11px] text-zinc-500 file:mr-3 file:border-0 file:bg-zinc-100 file:px-3 file:py-2 file:text-zinc-600"/><p>Video or JSONL · up to 100 MB. Video analysis uses model tokens.</p></div><Button size="sm" disabled={busy || !ready || !file || (file.name.toLowerCase().endsWith('.jsonl') ? activeReplay : activeVideo)} onClick={() => perform(() => start(file?.name.toLowerCase().endsWith('.jsonl') ? 'replay' : 'video'))}>{file?.name.toLowerCase().endsWith('.jsonl') ? 'Run observation checks' : 'Run experiment analysis'}</Button></div>
      {file && <label className="mt-4 flex items-center gap-2 text-xs text-zinc-500"><input type="checkbox" checked={partial} onChange={event => setPartial(event.target.checked)}/>This recording shows part of the procedure.</label>}
      {!sample && <details className="mt-5 text-xs text-zinc-500"><summary className="cursor-pointer">Try synthetic observations</summary><p className="mt-2! mb-3!">Illustrative observations are checked against the extracted methodology.</p><Button size="sm" variant="ghost" disabled={busy || !ready || activeReplay} onClick={() => perform(() => start('demo'))}>Run sample experiment</Button></details>}
    </section>}
    {run?.status === 'succeeded' && runProtocol ? <ExperimentDetail key={run.id} job={run} protocol={runProtocol} source={sources.find(source => source.id === runProtocol.source_id)} onSource={onSource} actions={actions}/>
      : run && <section aria-label="Experiment results" className="border-t border-line py-8"><h2 className={run.status === 'failed' ? 'text-fail' : 'text-running'}>{run.status === 'failed' ? 'Analysis needs attention' : 'Checking the recording'}</h2>{run.error && <p role="alert" className="mt-3! text-fail">{run.error}</p>}{['queued', 'running'].includes(run.status) && <p role="status" className="mt-3! muted">Analysis continues in the background. Expand the bar below to follow each stage.</p>}</section>}
    {!!data?.runs.length && <details className="mt-7 border-t border-line py-5 text-xs text-zinc-500"><summary className="cursor-pointer">Previous runs · {data.runs.length}</summary><div className="mt-3">{data.runs.map(item => <button key={item.id} onClick={() => setRunId(item.id)} className="flex w-full justify-between gap-3 border-b border-line py-3 text-left"><span>{item.filename} · {new Date(item.created_at).toLocaleString()}</span><span className={item.status === 'succeeded' ? 'text-pass' : item.status === 'failed' ? 'text-fail' : 'text-running'}>{humanize(item.status)}</span></button>)}</div></details>}
    {sample && <footer className="mt-5 flex flex-wrap items-center gap-4 border-t border-line pt-4 text-[10px] text-zinc-500"><a href={labSample.protocol} download className="underline">Download protocol PDF</a><a href={labSample.video} download className="underline">Download 30-second video</a><span><a href="https://huggingface.co/datasets/cong-lab/lsv" target="_blank" rel="noreferrer" className="underline">LabSuperVision / LabOS LSV</a> · CC BY-NC 4.0 · preparation excerpt</span></footer>}
  </div></div>{run && <ExperimentAnalysisDock job={run} protocol={runProtocol} onSkipDemo={canSkipDemo ? skipToDemo : undefined}/>}</div>;
}
