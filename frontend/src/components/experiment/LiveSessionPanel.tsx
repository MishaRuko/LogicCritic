'use client';

import { useEffect, useState } from 'react';
import QRCode from 'qrcode';
import { Button } from '@cloudflare/kumo';
import { CheckIcon, CopyIcon, DeviceMobileIcon, StopIcon } from '@phosphor-icons/react';
import * as api from '../../lib/research/api';
import type { ExperimentProtocol, ExperimentRun } from '../../types/api';

const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

/** The dashboard side of a live session: a QR code that opens the camera on a phone, and each
 *  protocol step as the analysis recognises it in the stream. */
export function LiveSessionPanel({
  run,
  protocol,
}: {
  run: ExperimentRun;
  protocol?: ExperimentProtocol;
}) {
  // Rendered only in the browser (it needs a live run), so the page origin is known here.
  // The parent keys this panel by run, so a new session gets a new link.
  const [link] = useState(() => `${window.location.origin}/live/${run.id}`);
  const [insecure] = useState(() => window.location.protocol !== 'https:');
  const [qr, setQr] = useState('');
  const [copied, setCopied] = useState(false);
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    void QRCode.toDataURL(link, { margin: 1, width: 220 }).then(setQr);
  }, [link]);

  const found = run.result.live ?? { observations: [], deviations: [] };
  const latest = new Map(found.observations.map(o => [o.step_id, o]));
  const analysed = run.result.processed_seconds;
  const ending = stopping;

  return (
    <section aria-label="Live session" className="border-t border-line py-8">
      <div className="grid gap-8 min-[900px]:grid-cols-[240px_minmax(0,1fr)]">
        <div>
          <h2 className="text-running">Live session</h2>
          <p className="muted mt-2! text-xs">
            Scan with the phone that will film the bench. It opens a camera page for this session.
          </p>
          {qr && (
            <img
              src={qr}
              width={220}
              height={220}
              alt="QR code that opens the camera page for this live session"
              className="mt-4 rounded-sm border border-line bg-white p-2"
            />
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              size="xs"
              variant="ghost"
              icon={copied ? CheckIcon : CopyIcon}
              disabled={!link}
              onClick={() =>
                navigator.clipboard.writeText(link).then(
                  () => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 1500);
                  },
                  () => undefined,
                )
              }
            >
              {copied ? 'Copied' : 'Copy link'}
            </Button>
            <a
              href={link || undefined}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs underline"
            >
              <DeviceMobileIcon size={14} />
              Use this device&rsquo;s camera
            </a>
          </div>
          {insecure && (
            <p className="mt-3 text-[11px] text-warn">
              Phones only allow the camera on https pages. Open this session from the deployed site,
              or reach this machine through an https tunnel.
            </p>
          )}
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p role="status" className="text-xs text-zinc-500">
              {analysed === undefined
                ? 'Waiting for the camera…'
                : `Analysed up to ${clock(analysed)} · feedback follows the camera by about 10–20 s`}
            </p>
            <Button
              size="sm"
              variant="outline"
              icon={StopIcon}
              disabled={ending}
              onClick={() => {
                setStopping(true);
                void api.stopLiveSession(run.id).catch(() => setStopping(false));
              }}
            >
              {ending ? 'Finishing…' : 'Stop session'}
            </Button>
          </div>
          <ol className="mt-5 space-y-2">
            {protocol?.protocol.steps.map((step, index) => {
              const seen = latest.get(step.id);
              const performed = seen?.status === 'performed';
              return (
                <li
                  key={step.id}
                  className="flex items-start gap-3 rounded-sm border border-line bg-white px-3 py-2 text-xs"
                >
                  <span
                    className={
                      performed
                        ? 'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-pass/10 text-pass'
                        : 'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-line text-[10px] text-zinc-500'
                    }
                  >
                    {performed ? <CheckIcon size={12} /> : index + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block">{step.description}</span>
                    {seen && (
                      <span className="mt-1 block text-zinc-500">
                        {seen.status.replace(/_/g, ' ')} at {clock(seen.span.start_s)}
                        {seen.description ? ` · ${seen.description}` : ''}
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ol>
          {!!found.deviations.length && (
            <div className="mt-6">
              <h3 className="text-xs font-medium text-fail">Deviations so far</h3>
              <ul className="mt-2 space-y-2">
                {found.deviations.map(deviation => (
                  <li key={deviation.id} className="text-xs text-fail">
                    {deviation.span ? `${clock(deviation.span.start_s)} · ` : ''}
                    {deviation.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
