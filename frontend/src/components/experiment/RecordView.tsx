import { useEffect, useState } from 'react';
import { ClipboardText, Collapsible, DropdownMenu } from '@cloudflare/kumo';
import { BracketsCurlyIcon, CaretDownIcon, DownloadSimpleIcon, FilePdfIcon } from '@phosphor-icons/react';
import QRCode from 'qrcode';
import { readable, time } from '../../lib/experiment/demo';
import { StatusIcon } from './Status';
import type { ExperimentRecord, VerificationResult } from '../../lib/experiment/types';

export function downloadJSON(value: unknown, filename: string) { const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
const text = (v: unknown) => typeof v === 'string' ? v : v && typeof v === 'object' ? Object.values(v).map(x => readable(String(x))).join(', ') : String(v);
const label = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
/** Prints the record alone; the document title becomes the suggested PDF filename. */
function downloadPDF(record: ExperimentRecord) {
  const title = document.title;
  document.title = `lens-${record.experimentId}-record`;
  const restore = () => { document.title = title; window.removeEventListener('afterprint', restore); };
  window.addEventListener('afterprint', restore);
  window.print();
}

export function DownloadMenu({ record }: { record: ExperimentRecord }) {
  return <DropdownMenu>
    <DropdownMenu.Trigger render={<button className="download-button"/>}><DownloadSimpleIcon size={15} weight="bold"/>Download<span className="download-divider"/><CaretDownIcon size={11} weight="bold"/></DropdownMenu.Trigger>
    <DropdownMenu.Content align="end" className="download-menu">
      <DropdownMenu.Item icon={FilePdfIcon} onClick={() => downloadPDF(record)}><span className="download-option"><strong>PDF</strong><small>Printable record with a QR code to the digital version</small></span></DropdownMenu.Item>
      <DropdownMenu.Item icon={BracketsCurlyIcon} onClick={() => downloadJSON(record, `lens-${record.experimentId}-record.json`)}><span className="download-option"><strong>JSON</strong><small>Full machine-readable record, re-verifiable from its hashes</small></span></DropdownMenu.Item>
    </DropdownMenu.Content>
  </DropdownMenu>;
}

export function RecordView({ record, onStep, shareUrl }: { record: ExperimentRecord; onStep?: (id: string) => void; shareUrl?: string }) {
  const { method, run } = record;
  // Share links (Trial's Cloudflare record store) are not part of LogicCritic yet; the QR code carries the record hash.
  const link = shareUrl;
  const [qr, setQr] = useState('');
  const payload = link ?? `lens://record/${record.recordHash}`;
  useEffect(() => { QRCode.toString(payload, { type: 'svg', margin: 0, errorCorrectionLevel: 'M', color: { dark: '#09090b', light: '#0000' } }).then(setQr); }, [payload]);
  const results = method.requirements.map(r => ({ r, result: record.results[r.id] as VerificationResult, o: run.observations.find(o => o.stepId === r.id) }));
  const counts = { verified: 0, contradicted: 0, skipped: 0, unverifiable: 0 } as Record<string, number>;
  for (const { result } of results) counts[result.status]++;
  const located = run.observations.filter(o => method.requirements.some(r => r.id === o.stepId));
  const confidence = located.length ? located.reduce((n, o) => n + o.confidence, 0) / located.length : 0;
  const caveats = [...new Set(method.requirements.flatMap(r => r.caveats ?? []))];
  const Step = onStep ? 'button' : 'div';

  return <div className="record-page">
    {!shareUrl && <div className="record-toolbar"><DownloadMenu record={record}/></div>}
    <article className="record">
      <header className="record-masthead"><span>Trial · Experiment record</span><span className="mono">No. {record.recordHash.slice(0, 8)}</span></header>
      <h1 className="record-title">{method.title}</h1>
      <p className="record-sub">{run.subtitle} · {method.source.split(' · ')[0].slice(0, 60)} · recorded {run.date} · {time(run.duration)}</p>

      <section className="record-hero">
        <div><span className="hero-figure">{counts.verified}<span className="hero-of">/{results.length}</span></span><span className="hero-label">steps verified</span></div>
        <div><span className="hero-figure">{confidence ? confidence.toFixed(2) : '—'}</span><span className="hero-label">mean confidence · {located.length} steps located</span></div>
        <ul className="hero-tally">{(['verified', 'contradicted', 'skipped', 'unverifiable'] as const).filter(s => s !== 'skipped' || counts.skipped).map(s => <li key={s} className={s}><StatusIcon status={s} size={12}/>{counts[s]} {s}</li>)}</ul>
      </section>

      {record.snapshot && (() => {
        const shot = record.snapshot, step = method.requirements.find(r => r.id === shot.stepId), status = step && record.results[step.id]?.status;
        return <figure className="record-snapshot">
          <img src={shot.image} alt={`Frame from the recording at ${time(shot.t)}`}/>
          <figcaption>
            <span className="mono">{time(shot.t)}{step && ` · step ${step.order.toString().padStart(2, '0')}`}</span>
            {step && <strong>{step.title}</strong>}
            {status && <span className={`step-status ${status}`}><StatusIcon status={status} size={12}/>{label(status)}</span>}
            {shot.caption && <p>{shot.caption}</p>}
          </figcaption>
        </figure>;
      })()}

      <section className="record-steps">
        {results.map(({ r, result, o }) => <Step key={r.id} className={`record-step ${onStep ? 'interactive' : ''}`} onClick={onStep ? () => onStep(r.id) : undefined}>
          <span className="mono step-no">{r.order.toString().padStart(2, '0')}</span>
          <span className="step-main"><strong>{r.title}</strong>
            {result.status === 'contradicted' && <em>Expected {text(result.expected)} — observed {text(result.observed)}</em>}
            {result.status === 'verified' && result.unconfirmedDetails?.length ? <em className="details-note">Details unconfirmed: {result.unconfirmedDetails.join('; ')}</em> : null}
            {result.status === 'skipped' && <em>Not performed between {time(result.between[0])} and {time(result.between[1])}: {result.reason}</em>}
            {result.status === 'unverifiable' && <em>{o ? `Not established: ${result.missingEvidence.map(readable).join(', ')}` : 'Not found in the recording'}</em>}
          </span>
          <span className="mono step-window">{o ? `${time(o.timestampStart)}–${time(o.timestampEnd)}` : '–'}</span>
          <span className={`step-status ${result.status}`}><StatusIcon status={result.status} size={12}/>{label(result.status)}</span>
          <span className="step-confidence">{o ? o.confidence.toFixed(2) : '—'}</span>
        </Step>)}
      </section>

      {caveats.length > 0 && <section className="record-caveats"><span className="record-kicker">Outside what video can establish</span><p>{caveats.join(' · ')}</p></section>}

      <section className="record-codes">
        <div className="qr" aria-label={link ? 'QR code linking to the digital record' : 'QR code of the record identifier'} dangerouslySetInnerHTML={{ __html: qr }}/>
        <dl>
          <dt>Digital record</dt><dd>{link ? <a className="record-url" href={link} target="_blank" rel="noreferrer">{link.replace(/^https?:\/\//, '')}</a> : <span className="muted">Not yet published</span>}</dd>
          <dt>Record hash</dt><dd><ClipboardText text={record.recordHash} size="sm"/></dd>
          <dt>Method hash</dt><dd><ClipboardText text={record.methodHash} size="sm"/></dd>
          <dt>Observations</dt><dd><ClipboardText text={record.observationHash} size="sm"/></dd>
          <dt>Generated</dt><dd className="mono">{new Date(record.generatedAt).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}</dd>
        </dl>
      </section>

      <Collapsible.Root className="record-limits">
        <Collapsible.DefaultTrigger>Limitations</Collapsible.DefaultTrigger>
        <Collapsible.DefaultPanel>{record.limitations.map(l => <p key={l}>{l}</p>)}<p className="mono">npm run verify-record -- lens-{record.experimentId}-record.json</p></Collapsible.DefaultPanel>
      </Collapsible.Root>
    </article>
  </div>;
}
