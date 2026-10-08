'use client';

import { useRef, useState } from 'react';
import { Button, cn } from '@cloudflare/kumo';
import {
  ArrowRightIcon,
  CheckIcon,
  DeviceMobileIcon,
  PlayIcon,
  UploadSimpleIcon,
} from '@phosphor-icons/react';
import * as api from '../../lib/research/api';
import { isLabSample, labSample } from '../../lib/research/demo';
import { humanize, sourceName } from '../../lib/research/graph';
import type { Experiments, Snapshot } from '../../types/api';
import { ExperimentDetail } from '../experiment/ExperimentDetail';
import { ExperimentAnalysisDock } from '../experiment/ExperimentAnalysisDock';
import { hasRecording } from '../../lib/experiment/bridge';
import { LiveSessionPanel } from '../experiment/LiveSessionPanel';
import { RecordingPreview } from '../experiment/RecordingPreview';
import { ProtocolPreview } from '../experiment/ProtocolProvenance';
import type { Perform } from './ResearchPanels';

export function ResearchStages({
  stage,
  hasResearch,
  verified,
  onChange,
}: {
  stage: number;
  hasResearch: boolean;
  verified: boolean;
  onChange: (stage: number) => void;
}) {
  return (
    <nav
      aria-label="Research workflow"
      className="research-stages flex items-center justify-center gap-3"
    >
      {['Add research', 'Verify research', 'Run experiment'].map((label, index) => (
        <div key={label} className="research-stage flex items-center gap-3">
          {index > 0 && <ArrowRightIcon size={14} className="text-zinc-300" aria-hidden />}
          <button
            aria-current={stage === index ? 'step' : undefined}
            disabled={index > 0 && !hasResearch}
            onClick={() => onChange(index)}
            className={cn(
              'flex items-center gap-2 rounded-md px-2 py-1.5 text-[11px] transition-colors',
              stage === index
                ? index === 2
                  ? 'bg-pass/5 text-pass'
                  : 'bg-zinc-100 text-ink'
                : 'text-zinc-500 hover:text-ink',
              index > 0 && !hasResearch && 'opacity-50',
            )}
          >
            <span
              className={cn(
                'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]',
                stage === index ? 'border-zinc-600 bg-zinc-700 text-white' : 'border-line',
                ((index === 0 && hasResearch) || (index === 1 && verified)) &&
                  'border-pass/20 bg-pass/10 text-pass',
              )}
            >
              {(index === 0 && hasResearch) || (index === 1 && verified) ? (
                <CheckIcon size={12} />
              ) : (
                index + 1
              )}
            </span>
            <span>{label}</span>
          </button>
        </div>
      ))}
    </nav>
  );
}

export function ResearchExperiments({
  state,
  data,
  loading,
  error,
  busy,
  perform,
  onSource,
  initialSourceId,
}: {
  initialSourceId?: string;
  state: Snapshot;
  data?: Experiments;
  loading: boolean;
  error: Error | null;
  busy: boolean;
  perform: Perform;
  onSource: (id: string) => void;
}) {
  const sources = state.sources.filter(source => source.origin !== 'lab-vision');
  const [sourceId, setSourceId] = useState(initialSourceId ?? '');
  const [runId, setRunId] = useState('');
  const [file, setFile] = useState<File>();
  const [partial, setPartial] = useState(false);
  const [setup, setSetup] = useState(!!initialSourceId);
  const [starting, setStarting] = useState(false);
  const startVersion = useRef(0);
  // The procedure the latest research run handed over is the one to follow, so it is the default.
  // The backend decides which that is, so an earlier run's protocol is never offered. The
  // dropdown still lets a person choose any other source.
  const suggested = sources.find(source => source.id === data?.suggested?.source_id);
  const retracted = (id?: string) => !!id && state.validity?.[id]?.status === 'invalidated';
  // By default, never pick a retracted paper when another source exists.
  const selectedSource = sourceId
    ? sources.find(source => source.id === sourceId)
    : (suggested ?? sources.find(source => !retracted(source.id)) ?? sources[0]);
  const sample = !!selectedSource && isLabSample(selectedSource.original_filename);
  const protocol = data?.protocols.find(p => p.source_id === selectedSource?.id && p.current);
  const run =
    data?.runs.find(r => r.id === runId) ??
    data?.runs.find(
      r => data.protocols.find(p => p.id === r.protocol_id)?.source_id === selectedSource?.id,
    );
  const runProtocol = data?.protocols.find(p => p.id === run?.protocol_id);
  const processing = state.jobs.some(job => ['queued', 'running'].includes(job.status));
  const ready = !!data && !processing && !!selectedSource;
  const activeVideo = data?.runs.some(
    r => r.mode === 'video' && ['queued', 'running'].includes(r.status),
  );
  const activeReplay = data?.runs.some(
    r => ['demo', 'replay'].includes(r.mode) && ['queued', 'running'].includes(r.status),
  );
  const activeLive = data?.runs.some(
    r => r.mode === 'live' && ['queued', 'running'].includes(r.status),
  );
  const completedDemo = data?.runs.find(
    item =>
      item.status === 'succeeded' &&
      (item.mode === 'video' || item.filename === 'DJI_08-first-30s.observations.jsonl') &&
      data.protocols.find(draft => draft.id === item.protocol_id)?.source_id === selectedSource?.id,
  );
  const canSkipDemo =
    (starting || (!!run && ['queued', 'running'].includes(run.status))) &&
    (!!completedDemo || (sample && ready && !busy && !activeReplay));
  async function start(mode: 'demo' | 'video' | 'replay' | 'live', sampleRun = false) {
    const version = ++startVersion.current;
    setStarting(true);
    try {
      let recording = file;
      if (sampleRun) {
        const live = mode === 'video';
        const response = await fetch(live ? labSample.video : labSample.observations);
        if (!response.ok)
          throw new Error('The sample recording or saved analysis could not be loaded.');
        recording = new File(
          [await response.blob()],
          live ? 'DJI_08-first-30s.mp4' : 'DJI_08-first-30s.observations.jsonl',
          { type: live ? 'video/mp4' : 'application/x-ndjson' },
        );
      }
      const created = await api.startExperiment(
        state.workspace.id,
        protocol?.id,
        mode,
        mode === 'live' ? undefined : recording,
        selectedSource!.id,
        sampleRun || partial,
      );
      if (version === startVersion.current) {
        setRunId(created.id);
        setSetup(false);
      }
    } finally {
      if (version === startVersion.current) setStarting(false);
    }
  }
  function skipToDemo() {
    if (completedDemo) {
      ++startVersion.current;
      setStarting(false);
      setRunId(completedDemo.id);
      setSetup(false);
    } else {
      void perform(() => start('replay', true));
    }
  }
  const actions = (
    <>
      {sample && (
        <>
          <Button
            size="sm"
            disabled={busy || !ready || activeVideo}
            onClick={() => perform(() => start('video', true))}
          >
            <PlayIcon size={14} className="mr-2" />
            Analyse sample live
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || !ready || activeReplay}
            onClick={() => perform(() => start('replay', true))}
          >
            {run ? 'Run sample again' : 'Run sample analysis'}
          </Button>
        </>
      )}
      {run && (
        <Button size="sm" variant="ghost" onClick={() => setSetup(value => !value)}>
          {setup ? 'Close setup' : 'New experiment'}
        </Button>
      )}
    </>
  );
  return (
    <div className="experiment-app experiment-layout h-full bg-paper">
      <div className="experiment-scroll px-5 py-7 sm:px-9">
        <div className="mx-auto max-w-[1440px]">
          {((setup && run) ||
            (!(run?.status === 'succeeded' && runProtocol) && (sample || run))) && (
            <div className="mb-6 flex flex-wrap justify-end gap-3">{actions}</div>
          )}
          {loading && (
            <p role="status" className="muted mb-5">
              Loading experiments…
            </p>
          )}
          {sourceId && !selectedSource && (
            <p role="status" className="muted mb-5">
              Loading the agent’s protocol…
            </p>
          )}
          {error && (
            <p role="alert" className="text-fail mb-5">
              {error.message}
            </p>
          )}
          {starting && (
            <p role="status" className="text-running mb-5">
              Extracting source-grounded methodology and preparing the recording…
            </p>
          )}
          {processing && (
            <p role="status" className="experiment-notice">
              Research is still processing. You can run the experiment once extraction finishes.
            </p>
          )}
          {(!run || setup) && (
            <section aria-label="Start experiment" className="mb-7">
              <h2 className="setup-heading">
                <span aria-hidden>1</span>
                Protocol
              </h2>
              <p className="setup-lead">The methodology the recording is checked against.</p>
              <div className="setup-card">
                {sources.length > 1 ? (
                  <label htmlFor="methodology-source" className="setup-label">
                    Methodology source
                  </label>
                ) : (
                  <p className="setup-label">Methodology source</p>
                )}
                <p className="setup-help">
                  The paper or protocol the steps are taken from. The research agent&rsquo;s
                  protocol is chosen by default when there is one.
                </p>
                {sources.length > 1 ? (
                  <select
                    id="methodology-source"
                    className="setup-select"
                    value={selectedSource?.id ?? ''}
                    onChange={event => {
                      setSourceId(event.target.value);
                      setRunId('');
                    }}
                  >
                    {sources.map(source => (
                      <option key={source.id} value={source.id}>
                        {retracted(source.id) ? '(Retracted) ' : ''}
                        {sourceName(source)}
                        {source.id === suggested?.id ? ' · suggested' : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  selectedSource && (
                    <p className="setup-select setup-select-static">{sourceName(selectedSource)}</p>
                  )
                )}
                {retracted(selectedSource?.id) && (
                  <p
                    role="alert"
                    className="mt-3 rounded-sm border border-fail/30 bg-fail/5 px-3 py-2 text-xs text-fail"
                  >
                    This paper has been retracted. A protocol taken from it may not be sound; choose
                    another source unless you are deliberately testing the retracted method.
                  </p>
                )}
                {selectedSource && (
                  <ProtocolPreview
                    source={selectedSource}
                    protocol={protocol}
                    sources={sources}
                    onSource={onSource}
                  />
                )}
              </div>
              {sample && !run && (
                <div className="grid gap-8 py-6 min-[1000px]:grid-cols-[minmax(0,1fr)_310px]">
                  <video
                    controls
                    preload="metadata"
                    src={labSample.video}
                    poster="/demo/lsv/DJI_08-preview.jpg"
                    aria-label="30-second sample recording"
                    className="w-full max-h-[50vh] bg-zinc-900 object-contain"
                  />
                  <div>
                    <h2>DJI_08 · first 30 seconds</h2>
                    <p className="muted mt-3!">
                      Cell preparation, paired with its original protocol.
                    </p>
                    <p className="muted mt-3!">
                      Analyse sample live to watch the methodology agent identify checks, the
                      recording split into frames, and the video agent inspect the evidence. Live
                      analysis uses model tokens.
                    </p>
                    <p className="muted mt-3!">
                      Run sample analysis replays saved observations for results in a few seconds,
                      without new model calls.
                    </p>
                  </div>
                </div>
              )}
              <h2 className="setup-heading mt-10!">
                <span aria-hidden>2</span>
                Record the experiment
              </h2>
              <p className="setup-lead">Upload a recording, or film it live from a phone.</p>
              <div className="grid gap-4 min-[900px]:grid-cols-2">
                <div className="setup-card">
                  <h3>
                    <UploadSimpleIcon size={18} aria-hidden />
                    Upload a recording
                  </h3>
                  <p>
                    A lab video, or saved observations as JSONL, up to 100 MB. Analysed in the
                    background; video analysis uses model tokens.
                  </p>
                  <label htmlFor="experiment-recording" className="sr-only">
                    Choose a lab video or saved observations
                  </label>
                  <div className="mt-4 flex min-w-0 items-center gap-3">
                    <input
                      id="experiment-recording"
                      type="file"
                      accept=".mp4,.mov,.webm,.avi,.m4v,.jsonl"
                      disabled={busy}
                      onChange={event => {
                        const next = event.target.files?.[0];
                        setFile(next);
                        setPartial(!!next?.name.includes('first-30s'));
                      }}
                      className="peer sr-only"
                    />
                    <label
                      htmlFor="experiment-recording"
                      aria-hidden
                      className="inline-flex shrink-0 cursor-pointer items-center gap-2 rounded-md border border-line bg-white px-3 py-1.5 text-xs hover:bg-zinc-50 peer-disabled:cursor-not-allowed peer-disabled:opacity-50 peer-focus-visible:ring-2 peer-focus-visible:ring-zinc-400"
                    >
                      {file ? 'Change recording' : 'Choose recording'}
                    </label>
                    <span className="truncate text-xs text-zinc-500">
                      {file ? file.name : 'No recording chosen'}
                    </span>
                  </div>
                  {file && !file.name.toLowerCase().endsWith('.jsonl') && (
                    <RecordingPreview file={file} />
                  )}
                  {file && (
                    <label className="mt-3 flex items-center gap-2 text-xs text-zinc-500">
                      <input
                        type="checkbox"
                        checked={partial}
                        onChange={event => setPartial(event.target.checked)}
                      />
                      This recording shows part of the procedure.
                    </label>
                  )}
                  <div className="mt-auto flex flex-wrap items-center gap-3 pt-5">
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={
                        busy ||
                        !ready ||
                        !file ||
                        (file.name.toLowerCase().endsWith('.jsonl') ? activeReplay : activeVideo)
                      }
                      onClick={() =>
                        perform(() =>
                          start(file?.name.toLowerCase().endsWith('.jsonl') ? 'replay' : 'video'),
                        )
                      }
                    >
                      {file?.name.toLowerCase().endsWith('.jsonl')
                        ? 'Run observation checks'
                        : 'Run experiment analysis'}
                    </Button>
                    {!busy &&
                      ready &&
                      (() => {
                        const replay = file?.name.toLowerCase().endsWith('.jsonl');
                        const why = !file
                          ? 'Choose a recording to run the analysis.'
                          : (replay ? activeReplay : activeVideo)
                            ? 'An analysis of this kind is already running; it can start when that one finishes.'
                            : undefined;
                        return why && <span className="text-[11px] text-zinc-500">{why}</span>;
                      })()}
                  </div>
                </div>
                <div className="setup-card">
                  <h3>
                    <DeviceMobileIcon size={18} aria-hidden />
                    Stream from a phone
                  </h3>
                  <p>
                    A phone films the bench and each step is checked as it happens. Sessions stop
                    after 15 minutes and use model tokens while they run.
                  </p>
                  <div className="mt-auto flex flex-wrap items-center gap-3 pt-5">
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={busy || !ready || activeLive}
                      onClick={() => perform(() => start('live'))}
                    >
                      Start live session
                    </Button>
                    {activeLive && (
                      <span className="text-[11px] text-zinc-500">
                        A live session is already running.
                      </span>
                    )}
                  </div>
                </div>
              </div>
              {!sample && (
                <details className="mt-5 text-xs text-zinc-500">
                  <summary className="cursor-pointer">
                    No recording yet? Try synthetic observations
                  </summary>
                  <p className="mt-2! mb-3!">
                    Illustrative observations are checked against the extracted methodology.
                  </p>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy || !ready || activeReplay}
                    onClick={() => perform(() => start('demo'))}
                  >
                    Run sample experiment
                  </Button>
                </details>
              )}
            </section>
          )}
          {setup && run ? null : run?.mode === 'live' &&
            ['queued', 'running'].includes(run.status) ? (
            <LiveSessionPanel key={run.id} run={run} protocol={runProtocol} />
          ) : run?.status === 'succeeded' && runProtocol ? (
            <ExperimentDetail
              key={run.id}
              job={run}
              protocol={runProtocol}
              source={sources.find(source => source.id === runProtocol.source_id)}
              sources={sources}
              onSource={onSource}
              actions={actions}
            />
          ) : (
            run && (
              <section aria-label="Experiment results" className="border-t border-line py-8">
                <h2 className={run.status === 'failed' ? 'text-fail' : 'text-running'}>
                  {run.status === 'failed' ? 'Analysis needs attention' : 'Checking the recording'}
                </h2>
                {hasRecording(run) && (
                  <RecordingPreview
                    src={`/api/experiment-runs/${run.id}/recording`}
                    filename={run.filename}
                  />
                )}{' '}
                {run.error && (
                  <p role="alert" className="mt-3! text-fail">
                    {run.error}
                  </p>
                )}
                {['queued', 'running'].includes(run.status) && (
                  <p role="status" className="mt-3! muted">
                    Analysis continues in the background. Expand the bar below to follow each stage.
                  </p>
                )}
              </section>
            )
          )}
          {!!data?.runs.length && (
            <details className="mt-7 border-t border-line py-5 text-xs text-zinc-500">
              <summary className="cursor-pointer">Previous runs · {data.runs.length}</summary>
              <div className="mt-3">
                {data.runs.map(item => (
                  <button
                    key={item.id}
                    onClick={() => setRunId(item.id)}
                    className="flex w-full justify-between gap-3 border-b border-line py-3 text-left"
                  >
                    <span>
                      {item.filename} · {new Date(item.created_at).toLocaleString()}
                    </span>
                    <span
                      className={
                        item.status === 'succeeded'
                          ? 'text-pass'
                          : item.status === 'failed'
                            ? 'text-fail'
                            : 'text-running'
                      }
                    >
                      {humanize(item.status)}
                    </span>
                  </button>
                ))}
              </div>
            </details>
          )}
          {sample && (
            <footer className="mt-5 flex flex-wrap items-center gap-4 border-t border-line pt-4 text-[10px] text-zinc-500">
              <a href={labSample.protocol} download className="underline">
                Download protocol PDF
              </a>
              <a href={labSample.video} download className="underline">
                Download 30-second video
              </a>
              <span>
                <a
                  href="https://huggingface.co/datasets/cong-lab/lsv"
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  LabSuperVision / LabOS LSV
                </a>{' '}
                · CC BY-NC 4.0 · preparation excerpt
              </span>
            </footer>
          )}
        </div>
      </div>
      {run && !(run.mode === 'live' && ['queued', 'running'].includes(run.status)) && (
        <ExperimentAnalysisDock
          key={run.id}
          job={run}
          protocol={runProtocol}
          onSkipDemo={canSkipDemo ? skipToDemo : undefined}
        />
      )}
    </div>
  );
}
