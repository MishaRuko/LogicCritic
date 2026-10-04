'use client';

import { useEffect, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import type { ExperimentProtocol, ExperimentRun } from '../../types/api';
import { time } from '../../lib/experiment/demo';

export function VideoAnalysisProgress({ job, protocol }: { job: ExperimentRun; protocol?: ExperimentProtocol }) {
  const [now, setNow] = useState(() => Date.now());
  const reducedMotion = useReducedMotion();
  const active = ['queued', 'running'].includes(job.status);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const complete = job.status === 'succeeded';
  const live = job.mode === 'video';
  const stage = job.result.analysis_stage;
  const method = job.result.agent_method;
  const frames = job.result.overview ?? [];
  const events = job.result.agent_events ?? [];
  const inspections = events.filter(event => event.kind === 'inspect');
  const elapsed = Math.max(0, ((job.completed_at ? Date.parse(job.completed_at) : now) - Date.parse(job.created_at)) / 1000);
  const current = complete ? 4 : stage === 'verification' ? 3 : stage === 'inspection' ? 2 : stage === 'frames' ? 1 : 0;
  const steps = protocol?.protocol.steps ?? [];
  const verdicts = Object.entries(job.result.agent_results ?? {});
  const phaseLabel = complete ? 'Analysis complete' : job.status === 'failed' ? 'Analysis stopped' : phasesLabel(current, live);
  const phases = live ? [
    ['Extract methodology', method ? `${method.requirements.length}${steps.length ? ` of ${steps.length}` : ''} steps with visible checks and caveats.` : `${steps.length || 'Source-grounded'} protocol steps. Methodology agent identifies what the camera can check.`],
    ['Split recording into frames', frames.length ? `${frames.length} overview frames${job.result.duration ? ` across ${time(job.result.duration)}` : ''}.` : 'Sample overview frames, then request closer views where needed.'],
    ['Inspect with the video agent', inspections.length ? `${inspections.length} closer inspections · ${inspections.reduce((sum, event) => sum + (event.count ?? 0), 0)} additional frames.` : 'Compare actions with each step and cite timestamped evidence.'],
    ['Check verifiability', 'Evaluate core checks and confidence: verified, contradicted, or unverifiable.'],
  ] : [
    ['Extract methodology', `${steps.length || 'Source-grounded'} protocol steps from the research source.`],
    ['Load observations', job.mode === 'replay' ? 'Replay saved observations. No new frames or agents are generated.' : 'Load synthetic sample observations.'],
    ['Check the evidence', 'Compare recorded observations against the protocol checks.'],
    ['Check verifiability', 'Keep unreadable values and incomplete evidence marked for review.'],
  ];
  return <section aria-label="Analysis pipeline" className="border-t border-line py-6">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 className={job.status === 'failed' ? 'contradicted' : complete ? 'text-pass' : 'text-running'}>{job.status === 'failed' ? 'Analysis needs attention' : complete ? 'Analysis complete' : 'Checking the recording'}</h2>
      <span className="mono muted text-xs">{complete ? 'Completed in' : 'Elapsed'} {time(elapsed)}</span>
    </div>
    {job.error && <p role="alert" className="contradicted mt-3!">{job.error}</p>}
    <ol aria-label="Analysis stages" className="mt-6 grid min-w-0 gap-x-6 gap-y-8 sm:grid-cols-2 lg:grid-cols-4">
      {phases.map(([label, description], index) => <li key={label} aria-current={active && index === current ? 'step' : undefined} className="min-w-0">
        <div aria-label={`${label} output`} className="mb-4 h-64 min-w-0 overflow-hidden border-b border-line pb-4">
          {index === 0 ? <div className="h-full overflow-y-auto pr-2">
            {method?.summary && <p className="mb-3! line-clamp-3 text-[11px] leading-5 text-zinc-500">{method.summary}</p>}
            {!!steps.length ? <ol className="space-y-4">{steps.map((step, stepIndex) => {
              const requirement = method?.requirements.find(item => item.id === step.id);
              return <motion.li key={step.id} initial={{ opacity: 0, y: reducedMotion ? 0 : 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reducedMotion ? 0 : .25, delay: reducedMotion ? 0 : Math.min(stepIndex * .025, .3) }} className="flex gap-2 text-[11px] leading-5">
                <span className="mono shrink-0 text-zinc-400">{String(stepIndex + 1).padStart(2, '0')}</span><div><p>{step.description}</p>{requirement ? <><ul className="mt-1 space-y-1 text-zinc-500">{requirement.checks?.map(check => <li key={check}>↳ {check}</li>)}</ul>{!!requirement.caveats?.length && <p className="mt-1! text-[10px] text-warn">Cannot establish: {requirement.caveats.join(' · ')}</p>}</> : active && live && <span className="text-[10px] text-zinc-400">Identifying visible checks…</span>}</div>
              </motion.li>;
            })}</ol> : <StageWaiting active={active && current === 0} text="Reading the research source…"/>}
          </div> : index === 1 ? frames.length ? <div aria-label="Recording overview" className="h-full overflow-y-auto pr-1"><div className="grid grid-cols-[repeat(2,minmax(0,96px))] gap-x-2 gap-y-3">{frames.map(frame => <motion.figure key={frame.t} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: reducedMotion ? 0 : .3 }} className="min-w-0"><img src={`data:image/jpeg;base64,${frame.data}`} alt={`Recording at ${time(frame.t)}`} className="aspect-[4/3] w-full rounded-sm bg-zinc-100 object-cover"/><figcaption className="mono mt-1 text-zinc-500">{time(frame.t)}</figcaption></motion.figure>)}</div></div>
            : !live && job.result.observations?.length ? <ol className="h-full space-y-3 overflow-y-auto text-[11px] leading-5">{job.result.observations.map(observation => <li key={observation.id}><span className="mono text-zinc-400">{time(observation.span.start_s)}–{time(observation.span.end_s)}</span><p>{observation.description ?? observation.status}</p></li>)}</ol>
            : <StageWaiting active={active && current === 1} text={live ? 'Overview frames will appear here as the recording is sampled.' : 'Saved observations will appear here.'}/>
          : index === 2 ? <div className="h-full overflow-y-auto pr-2">
            {events.length ? <ol aria-label="Agent inspection activity" className="space-y-4 text-[11px] leading-5">{events.map((event, eventIndex) => <motion.li key={eventIndex} initial={{ opacity: 0, y: reducedMotion ? 0 : 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: reducedMotion ? 0 : .25 }}>
              <span className={`mb-1 block text-[10px] ${event.kind === 'done' ? 'text-pass' : event.kind === 'retry' ? 'text-warn' : 'text-running'}`}>{event.kind === 'overview' ? 'Video agent started' : event.kind === 'inspect' ? 'Closer inspection' : event.kind === 'retry' ? 'Reviewing findings' : 'Evidence collected'}</span>
              {event.kind === 'overview' ? `Sampled ${event.frames} overview frames` : event.kind === 'inspect' ? `Inspect ${time(event.start ?? 0)}–${time(event.end ?? 0)} · ${event.count} frames` : event.kind === 'retry' ? event.message : 'Findings submitted for all methodology steps'}
            </motion.li>)}</ol> : <StageWaiting active={active && current === 2} text={live ? 'The video agent will request closer views and gather evidence for each step.' : 'The protocol verifier checks the saved evidence.'}/>}
            {!!job.result.agent_observations?.length && <ol className="mt-4 space-y-3 border-t border-line pt-3 text-[11px] leading-5">{job.result.agent_observations.map(observation => <li key={observation.id}><span className="mono text-zinc-400">{time(observation.timestampStart)}–{time(observation.timestampEnd)}</span><p>{observation.summary}</p></li>)}</ol>}
          </div> : <div className="h-full overflow-y-auto pr-2">
            {verdicts.length ? <><div className="mb-4 flex flex-wrap gap-x-3 gap-y-1 text-[10px]">{(['verified', 'contradicted', 'unverifiable'] as const).map(status => <span key={status} className={status}>{verdicts.filter(([, result]) => result.status === status).length} {status}</span>)}</div><ol className="space-y-3 text-[11px] leading-5">{verdicts.map(([id, result]) => <li key={id}><span className={`block text-[10px] ${result.status}`}>{result.status === 'verified' ? 'Verified' : result.status === 'contradicted' ? 'Contradicted' : 'Unverifiable'}</span><p>{method?.requirements.find(step => step.id === id)?.title ?? steps.find(step => step.id === id)?.description ?? id}</p>{result.status === 'unverifiable' && <p className="mt-1! text-[10px] text-zinc-400">{result.reason}</p>}</li>)}</ol></>
              : complete && job.result.summary ? <div className="text-[11px] leading-6"><p>{job.result.summary.observations} observations checked</p><p>{job.result.summary.deviations} findings · {job.result.summary.needs_review} need review</p>{job.result.deviations?.map(finding => <p key={finding.id} className="mt-3! text-warn">{finding.message}</p>)}</div>
              : <StageWaiting active={active && current === 3} text="Findings appear when evidence and confidence have been checked. Missing or unreadable evidence stays unverifiable."/>}
          </div>}
        </div>
        <div className={`mb-2 text-2xl ${index < current ? 'text-pass' : index === current && active ? 'text-running' : 'muted'}`}>{String(index + 1).padStart(2, '0')}</div>
        <h3 className="text-xs">{label}</h3><p className="muted small mt-2!">{description}</p>
      </li>)}
    </ol>
    <div className="mt-7 border-t border-line pt-4">
      <div className="flex items-center justify-between gap-3 text-[11px]"><p className="text-zinc-500">Progress <span className="mx-2 text-zinc-300">·</span><span className={complete ? 'text-pass' : job.status === 'failed' ? 'text-fail' : 'text-zinc-600'}>{phaseLabel}</span></p><span className="text-[10px] text-zinc-400">{current} of 4 stages complete</span></div>
      <div role="progressbar" aria-label="Experiment analysis progress" aria-valuemin={0} aria-valuemax={4} aria-valuenow={current} aria-valuetext={phaseLabel} className="mt-3 flex gap-1.5">
        {phases.map(([label], index) => <motion.span key={label} aria-hidden initial={false} animate={{ backgroundColor: index < current ? '#4d7963' : index === current && active ? '#60a5fa' : '#e4e4e7', opacity: index === current && active && !reducedMotion ? [0.45, 1, 0.45] : 1 }} transition={{ backgroundColor: { duration: reducedMotion ? 0 : .35 }, opacity: { duration: 1.8, repeat: active && index === current && !reducedMotion ? Infinity : 0 } }} className="h-[3px] min-w-0 flex-1 rounded-full"/>)}
      </div>
      <div aria-hidden className="mt-1.5 flex justify-between text-[9px] text-zinc-400"><span>Research source</span><span>Evidence assessed</span></div>
    </div>
    {active && <p role="status" className="muted small mt-5!">{job.status === 'queued' ? 'Waiting for an experiment worker.' : live && current === 0 ? 'Methodology agent is identifying visible checks and conditions the recording cannot establish.' : live && current === 1 ? 'Decoding and sampling the recording.' : live && current === 2 ? 'Video agent is inspecting the frames and collecting evidence for each step.' : 'Checking the available evidence.'} Analysis continues in the background.</p>}
    {complete && job.result.agent_results && <p className="muted small mt-5!">{Object.values(job.result.agent_results).filter(result => result.status === 'verified').length} verified · {Object.values(job.result.agent_results).filter(result => result.status === 'contradicted').length} contradicted · {Object.values(job.result.agent_results).filter(result => result.status === 'unverifiable').length} unverifiable. Open a step in Execution to inspect its evidence.</p>}
  </section>;
}

function StageWaiting({ active, text }: { active: boolean; text: string }) {
  return <div className="flex h-full flex-col justify-center gap-3 px-1"><span aria-hidden className={`h-1.5 w-1.5 rounded-full ${active ? 'bg-running motion-safe:animate-pulse' : 'bg-zinc-300'}`}/><p className="text-[11px] leading-5 text-zinc-400">{text}</p></div>;
}

function phasesLabel(current: number, live: boolean) {
  return (live ? ['Extracting methodology', 'Sampling frames', 'Inspecting evidence', 'Checking verifiability'] : ['Preparing methodology', 'Loading observations', 'Checking evidence', 'Checking verifiability'])[current];
}
