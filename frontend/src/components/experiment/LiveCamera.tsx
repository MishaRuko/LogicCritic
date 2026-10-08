'use client';

import { useEffect, useRef, useState } from 'react';
import { CameraIcon, StopIcon } from '@phosphor-icons/react';
import * as api from '../../lib/research/api';

const FRAME_EVERY_MS = 1000; // one still per second is what the analysis samples anyway
const SEND_EVERY_MS = 2000;
const PIECE_EVERY_MS = 5000; // recording pieces stay small, far below any upload limit
const FRAME_MAX_SIDE = 1024;
// H.264 MP4 first: it plays everywhere, Safari included. Chrome otherwise picks VP9.
const RECORDING_TYPES = [
  'video/mp4;codecs=avc1',
  'video/mp4',
  'video/webm;codecs=vp9',
  'video/webm;codecs=vp8',
  'video/webm',
];

type Phase = 'loading' | 'ready' | 'starting' | 'streaming' | 'stopping' | 'stopped' | 'error';
type Frame = { blob: Blob; seconds: number };

const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;

/** The phone's side of a live session: a full-screen viewfinder that sends one still frame a
 *  second for analysis and uploads its own recording in small pieces while it films. */
export function LiveCamera({ runId }: { runId: string }) {
  const [phase, setPhase] = useState<Phase>('loading');
  const [message, setMessage] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [limit, setLimit] = useState(15 * 60);
  const limitRef = useRef(15 * 60); // read by the timer, which outlives renders
  const [backlog, setBacklog] = useState(0);
  const [offline, setOffline] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const started = useRef(0);
  const timers = useRef<number[]>([]);
  const frames = useRef<Frame[]>([]);
  const pieces = useRef<Blob[]>([]);
  const nextPiece = useRef(0);
  const uploading = useRef<Promise<void> | null>(null);
  const sending = useRef(false);
  const wakeLock = useRef<WakeLockSentinel | null>(null);
  const stopping = useRef(false);

  useEffect(() => {
    api
      .fetchExperimentRun(runId)
      .then(found => {
        if (found.mode !== 'live') throw new Error('This link is not a live session.');
        if (!['queued', 'running'].includes(found.status))
          throw new Error('This live session has already ended.');
        setPhase('ready');
      })
      .catch(error => {
        setMessage(error instanceof Error ? error.message : String(error));
        setPhase('error');
      });
    return () => release();
  }, [runId]);

  function release() {
    for (const id of timers.current) window.clearInterval(id);
    timers.current = [];
    stream.current?.getTracks().forEach(track => track.stop());
    void wakeLock.current?.release().catch(() => undefined);
    wakeLock.current = null;
  }

  async function keepAwake() {
    try {
      wakeLock.current = (await navigator.wakeLock?.request('screen')) ?? null;
    } catch {
      /* Not every browser allows it; the session still works while the screen is on. */
    }
  }

  function captureFrame() {
    const element = video.current;
    if (!element || !element.videoWidth) return;
    const scale = Math.min(1, FRAME_MAX_SIDE / Math.max(element.videoWidth, element.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(element.videoWidth * scale);
    canvas.height = Math.round(element.videoHeight * scale);
    canvas.getContext('2d')?.drawImage(element, 0, 0, canvas.width, canvas.height);
    const seconds = (performance.now() - started.current) / 1000;
    canvas.toBlob(blob => blob && frames.current.push({ blob, seconds }), 'image/jpeg', 0.75);
  }

  async function sendFrames() {
    if (sending.current || !frames.current.length) return;
    sending.current = true;
    const batch = frames.current.splice(0);
    try {
      const sent = await api.sendLiveFrames(runId, batch);
      setLimit(sent.max_seconds);
      limitRef.current = sent.max_seconds;
      setOffline(false);
    } catch (error) {
      if (error instanceof api.ApiError && error.status === 409) {
        void stop(false); // ended from the dashboard
      } else {
        frames.current.unshift(...batch.slice(-30)); // keep the last half minute and retry
        setOffline(true);
      }
    } finally {
      sending.current = false;
    }
  }

  /** Upload recording pieces strictly in order, retrying each until it is stored. */
  function uploadPieces(): Promise<void> {
    if (uploading.current) return uploading.current;
    uploading.current = (async () => {
      while (pieces.current.length) {
        try {
          await api.sendRecordingPiece(runId, nextPiece.current, pieces.current[0]);
          pieces.current.shift();
          nextPiece.current += 1;
          setOffline(false);
        } catch (error) {
          if (error instanceof api.ApiError && error.status === 409 && stopping.current) break;
          setOffline(true);
          await new Promise(resolve => window.setTimeout(resolve, 2000));
        }
        setBacklog(pieces.current.length);
      }
    })().finally(() => {
      uploading.current = null;
    });
    return uploading.current;
  }

  async function start() {
    setPhase('starting');
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
      stream.current = media;
      video.current!.srcObject = media;
      await video.current!.play();
      const type = RECORDING_TYPES.find(item => MediaRecorder.isTypeSupported?.(item));
      const mediaRecorder = new MediaRecorder(media, {
        ...(type ? { mimeType: type } : {}),
        videoBitsPerSecond: 1_000_000,
      });
      mediaRecorder.ondataavailable = event => {
        if (!event.data.size) return;
        const piece = event.data.type
          ? event.data
          : new Blob([event.data], { type: mediaRecorder.mimeType || 'video/webm' });
        pieces.current.push(piece);
        setBacklog(pieces.current.length);
        void uploadPieces();
      };
      recorder.current = mediaRecorder;
      started.current = performance.now();
      mediaRecorder.start(PIECE_EVERY_MS);
      await keepAwake();
      timers.current = [
        window.setInterval(captureFrame, FRAME_EVERY_MS),
        window.setInterval(() => void sendFrames(), SEND_EVERY_MS),
        window.setInterval(() => {
          const seconds = (performance.now() - started.current) / 1000;
          setElapsed(seconds);
          if (seconds >= limitRef.current) void stop();
        }, 500),
      ];
      setPhase('streaming');
    } catch (error) {
      release();
      setMessage(
        error instanceof DOMException && error.name === 'NotAllowedError'
          ? 'Camera access was refused. Allow the camera for this site in the browser settings, then reload.'
          : error instanceof Error
            ? error.message
            : String(error),
      );
      setPhase('error');
    }
  }

  async function stop(tellServer = true) {
    if (stopping.current) return;
    stopping.current = true;
    setPhase('stopping');
    for (const id of timers.current) window.clearInterval(id);
    timers.current = [];
    const mediaRecorder = recorder.current;
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      await new Promise<void>(resolve => {
        mediaRecorder.addEventListener('stop', () => resolve(), { once: true });
        mediaRecorder.stop(); // emits the last piece first
      });
    }
    await sendFrames();
    await uploadPieces();
    if (tellServer) await api.stopLiveSession(runId).catch(() => undefined);
    release();
    setPhase('stopped');
  }

  useEffect(() => {
    function refresh() {
      if (document.visibilityState === 'visible' && phase === 'streaming') void keepAwake();
    }
    document.addEventListener('visibilitychange', refresh);
    return () => document.removeEventListener('visibilitychange', refresh);
  }, [phase]);

  const remaining = Math.max(0, limit - elapsed);
  return (
    <main className="fixed inset-0 flex flex-col bg-black text-white">
      <video
        ref={video}
        playsInline
        muted
        aria-label="Camera viewfinder"
        className="absolute inset-0 h-full w-full object-cover"
      />
      <header className="relative z-10 flex items-center justify-between gap-3 bg-gradient-to-b from-black/70 to-transparent px-4 pt-4 pb-8 text-sm">
        <div className="min-w-0">
          <p className="truncate font-medium">Live session</p>
          <p className="truncate text-xs text-white/70">Trial · keep the phone still</p>
        </div>
        {phase === 'streaming' && (
          <p className="flex shrink-0 items-center gap-2 font-mono text-xs" role="status">
            <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" aria-hidden />
            {clock(elapsed)} · {clock(remaining)} left
          </p>
        )}
      </header>
      <div className="relative z-10 mt-auto bg-gradient-to-t from-black/80 to-transparent px-4 pt-10 pb-8 text-center">
        {phase === 'loading' && <p className="text-sm text-white/80">Opening the session…</p>}
        {phase === 'ready' && (
          <>
            <p className="mx-auto mb-5 max-w-sm text-sm text-white/80">
              Point the camera at the bench and keep the phone still. The dashboard shows each step
              as it is recognised.
            </p>
            <button
              onClick={() => void start()}
              className="inline-flex items-center gap-2 rounded-full bg-white px-6 py-3 text-sm font-medium text-black"
            >
              <CameraIcon size={18} weight="fill" />
              Start camera
            </button>
          </>
        )}
        {phase === 'starting' && <p className="text-sm text-white/80">Starting the camera…</p>}
        {phase === 'streaming' && (
          <>
            {(offline || backlog > 1) && (
              <p className="mb-4 text-xs text-amber-300" role="status">
                {offline ? 'Connection lost, retrying. Keep filming. ' : ''}
                {backlog > 1 ? `${backlog} recording pieces waiting to upload.` : ''}
              </p>
            )}
            <button
              onClick={() => void stop()}
              aria-label="Stop live session"
              className="inline-flex items-center gap-2 rounded-full bg-red-600 px-6 py-3 text-sm font-medium"
            >
              <StopIcon size={18} weight="fill" />
              Stop
            </button>
          </>
        )}
        {phase === 'stopping' && (
          <p className="text-sm text-white/80" role="status">
            Finishing the upload{backlog ? ` (${backlog} pieces left)` : ''}. Keep this page open.
          </p>
        )}
        {phase === 'stopped' && (
          <p className="mx-auto max-w-sm text-sm" role="status">
            Session ended. The results are completing on the dashboard; you can close this page.
          </p>
        )}
        {phase === 'error' && (
          <p className="mx-auto max-w-sm text-sm text-red-300" role="alert">
            {message}
          </p>
        )}
      </div>
    </main>
  );
}
