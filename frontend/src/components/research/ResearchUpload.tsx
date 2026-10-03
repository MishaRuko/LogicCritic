'use client';

import { useId, useState } from 'react';
import { Button, cn } from '@cloudflare/kumo';
import { ArrowUpIcon, CheckIcon, FileTextIcon, PaperclipIcon, XIcon } from '@phosphor-icons/react';
import type { UploadProgress } from '../../lib/research/api';
import type { Snapshot } from '../../types/api';

export function ResearchUpload({ busy, submit, processing = false }: {
  busy: boolean;
  processing?: boolean;
  submit: (files: File[]) => Promise<boolean>;
}) {
  const inputId = useId();
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState('');
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState('');
  const [fileKey, setFileKey] = useState(0);

  function choose(chosen: File[]) {
    setError('');
    setFiles(chosen);
  }

  async function add() {
    const documents = [...files, ...(text.trim() ? [new File([text.trim()], 'research-notes.txt', { type: 'text/plain' })] : [])];
    if (!documents.length || busy) return;
    setError('');
    for (const file of documents) {
      if (!/\.(txt|md|markdown|pdf)$/i.test(file.name)) { setError('Choose a PDF, Markdown or text file.'); return; }
      if (!file.size) { setError(`${file.name} is empty. Choose a file with some text.`); return; }
      if (file.size > 10 * 1024 * 1024) { setError(`${file.name} is too large. The limit is 10 MB per file.`); return; }
    }
    if (await submit(documents)) {
      setFiles([]); setText(''); setFileKey(k => k + 1);
    }
  }

  return <div aria-label="Add research" className={cn('research-composer border bg-white transition-colors', (busy || processing) && 'research-composer-processing', dragging ? 'border-blue-300 bg-blue-50/30' : 'border-zinc-300')} onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (!busy) choose(Array.from(e.dataTransfer.files)); }}>
    {!!files.length && <ul aria-label="Selected documents" className="flex max-h-24 flex-wrap gap-2 overflow-y-auto px-4 pt-3">{files.map((file, index) => <li key={`${file.name}-${index}`} className="flex max-w-full items-center gap-2 rounded-sm border border-line bg-zinc-50 px-3 py-2 text-xs"><FileTextIcon size={16} className="shrink-0 text-zinc-500"/><span className="min-w-0 truncate">{file.name}</span><span className="shrink-0 text-[10px] text-zinc-400">{file.size < 1024 * 1024 ? `${Math.max(1, Math.round(file.size / 1024))} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`}</span><button type="button" aria-label={`Remove ${file.name}`} disabled={busy} className="shrink-0 text-zinc-500" onClick={() => choose(files.filter((_, i) => i !== index))}><XIcon size={14}/></button></li>)}</ul>}
    <textarea aria-label="Research text" placeholder={dragging ? 'Drop your paper here' : 'Paste research text, or attach a PDF…'} disabled={busy} rows={2} className="block max-h-36 min-h-16 w-full resize-none border-0 bg-transparent px-4 pt-4 pb-2 text-sm leading-relaxed outline-none placeholder:text-zinc-400" value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void add(); } }}/>
    {error && <p role="alert" className="px-4 pb-2 text-xs text-fail">{error}</p>}
    <div className="flex items-center justify-between gap-3 px-3 pb-3"><div className="flex min-w-0 items-center gap-3">
      <label htmlFor={inputId} title="Attach a PDF or research notes" className={cn('relative flex h-8 w-8 shrink-0 items-center justify-center rounded-sm text-zinc-500 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-blue-400', busy ? 'opacity-50' : 'cursor-pointer hover:bg-zinc-100')}>
        <PaperclipIcon size={20}/><span className="sr-only">Attach files</span>
        <input id={inputId} key={fileKey} aria-label="Research documents" type="file" accept=".pdf,application/pdf,.md,.markdown,.txt,text/plain,text/markdown" multiple disabled={busy} onChange={e => choose(Array.from(e.target.files ?? []))} className="absolute inset-0 w-full cursor-pointer opacity-0"/>
      </label>
      <span className="text-[10px] text-zinc-400 max-[500px]:hidden">PDF, Markdown, TXT · up to 10 MB</span>
    </div><Button size="sm" variant="primary" shape="square" className="h-8! w-8! rounded-md! bg-blue-600! text-white! hover:bg-blue-700!" icon={<ArrowUpIcon size={17}/>} aria-label="Build argument" title="Upload and build argument" loading={busy} disabled={busy || (!files.length && !text.trim())} onClick={() => void add()}/></div>
  </div>;
}

export function ResearchProgress({ progress, state, busy, onCancel }: { progress?: UploadProgress; state?: Snapshot; busy: boolean; onCancel: () => void }) {
  const jobs = (state?.jobs ?? []).filter(job => !progress?.jobIds?.length || progress.jobIds.includes(job.id));
  const active = jobs.filter(job => !['succeeded', 'failed', 'cancelled'].includes(job.status));
  if (!progress && !active.length) return null;
  const transferring = progress && ['uploading', 'processing'].includes(progress.stage);
  const latest = [...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const extractionRequested = progress?.stage === 'queued' || !progress;
  const failed = progress?.stage === 'failed' || (!active.length && extractionRequested && latest && ['failed', 'cancelled'].includes(latest.status));
  const complete = !failed && !transferring && !active.length && (progress?.stage === 'saved' || extractionRequested && latest?.status === 'succeeded');
  const completed = active.reduce((sum, job) => sum + job.completed_chunks, 0);
  const total = active.reduce((sum, job) => sum + job.total_chunks, 0);
  const hasClaims = !!state?.graph.statements.length;
  const percent = transferring ? progress.percent : complete ? 100 : total ? Math.min(100, Math.round(completed / total * 100)) : undefined;
  const title = failed ? 'Processing stopped' : transferring ? progress.stage === 'uploading' ? `Uploading ${progress.total} ${progress.total === 1 ? 'file' : 'files'}` : 'Reading the document' : complete ? progress?.stage === 'saved' ? 'Research saved' : 'Argument graph ready' : hasClaims ? 'Building the argument graph' : 'Extracting claims and evidence';
  const detail = failed ? latest?.error ?? 'Your material is still available. Retry from Material.' : transferring ? progress.filename : complete ? progress?.stage === 'saved' ? 'Open Material when you’re ready to build its argument.' : 'Select a claim to explore its evidence and reasoning.' : hasClaims ? `${state!.graph.statements.length} claims added · new connections appear as extraction continues` : active.every(job => job.status === 'queued') ? 'Waiting for extraction to start…' : 'Finding statements and the evidence that supports them…';
  return <div role="status" className={cn('research-progress-card relative overflow-hidden border border-line bg-white px-4 pt-3 pb-5', failed && 'border-red-200')}>
    <div className="flex items-center justify-between gap-4"><div className="min-w-0"><p className="flex items-center gap-2 text-sm font-normal">{complete ? <CheckIcon size={16} className="text-pass"/> : !failed && <span className="research-processing-dot"/>}{title}</p><p className="mt-1.5 truncate text-xs text-zinc-500" title={detail}>{percent !== undefined ? `${percent}% · ` : ''}{detail}</p></div>{!!active.length && <button aria-label="Cancel extraction" disabled={busy} onClick={onCancel} className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-zinc-400 hover:bg-zinc-100"><XIcon size={18}/></button>}</div>
    <div role="progressbar" aria-label={transferring ? 'Document upload progress' : 'Argument extraction progress'} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} className="research-progress-track absolute right-0 bottom-0 left-0 h-0.5 overflow-hidden bg-zinc-100"><div className={cn('research-stage-bar h-full bg-blue-400 transition-[width] duration-700', percent === undefined && 'research-stage-indeterminate', !complete && !failed && 'research-stage-active')} style={{ width: percent !== undefined ? `${percent}%` : '35%' }}/></div>
  </div>;
}

export function ExtractionProgress({ completed, total, queued = false }: { completed: number; total: number; queued?: boolean }) {
  const percent = total > 0 ? Math.min(100, Math.round(completed / total * 100)) : 0;
  return <div className="mt-4"><div className="mb-2 flex items-center justify-between gap-2 text-xs text-zinc-500"><span>{queued ? 'Waiting to start' : percent === 100 ? 'Finishing argument…' : 'Building argument'}</span><span className="tabular-nums">{queued || !total ? 'Queued' : `${percent}%`}</span></div><progress aria-label="Argument extraction progress" value={percent} max={100} className="research-progress block h-0.5 w-full"/></div>;
}
