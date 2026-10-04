import type { Frame } from './agent';

/** Browser frame sampling: seek a detached <video> and draw each frame to a canvas. */
export async function openVideo(src: string): Promise<HTMLVideoElement> {
  const video = document.createElement('video');
  video.muted = true; video.preload = 'auto'; video.playsInline = true; video.crossOrigin = 'anonymous'; video.src = src;
  await new Promise<void>((resolve, reject) => {
    video.addEventListener('loadeddata', () => resolve(), { once: true });
    video.addEventListener('error', () => reject(new Error('This browser cannot decode the video. Try an H.264 MP4.')), { once: true });
  });
  return video;
}

export async function grabFrame(video: HTMLVideoElement, t: number, width: number): Promise<Frame> {
  const time = Math.max(0, Math.min(video.duration - 0.05, t));
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Seeking to ${time.toFixed(1)} s timed out.`)), 8000);
    video.addEventListener('seeked', () => { clearTimeout(timer); resolve(); }, { once: true });
    video.currentTime = time;
  });
  const canvas = document.createElement('canvas');
  canvas.width = Math.min(width, video.videoWidth);
  canvas.height = Math.round(canvas.width * video.videoHeight / video.videoWidth);
  canvas.getContext('2d')!.drawImage(video, 0, 0, canvas.width, canvas.height);
  return { t: time, data: canvas.toDataURL('image/jpeg', 0.75).split(',')[1] };
}

export async function grabFrames(video: HTMLVideoElement, times: number[], width: number): Promise<Frame[]> {
  const frames: Frame[] = [];
  for (const t of times) frames.push(await grabFrame(video, t, width));
  return frames;
}

/** Evenly spaced timestamps, centred in each slice so the first and last frames are not black. */
export function spacedTimes(start: number, end: number, count: number): number[] {
  const step = (end - start) / count;
  return Array.from({ length: count }, (_, i) => start + step * (i + 0.5));
}

export function overviewCount(duration: number): number { return Math.max(8, Math.min(40, Math.round(duration / 3))); }
