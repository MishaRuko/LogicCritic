import type { MethodContract, Run } from './types';

export type Analysis = {
  id: string; createdAt: string; methodName: string; videoName: string;
  method: MethodContract; run: Run;
  /** How observations were produced. */
  mode: 'agent' | 'annotation';
  /** Uploaded video bytes live in IndexedDB; samples point at /demo. */
  videoStored?: boolean;
  log?: string[];
};

const KEY = 'lens-analyses-v2';
export function loadAnalyses(): Analysis[] { try { return JSON.parse(localStorage.getItem(KEY) ?? '[]'); } catch { return []; } }
export function saveAnalyses(analyses: Analysis[]) { localStorage.setItem(KEY, JSON.stringify(analyses)); }

function db(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('lens', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('videos');
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
}
export async function putVideo(id: string, blob: Blob) {
  const d = await db();
  await new Promise<void>((resolve, reject) => { const tx = d.transaction('videos', 'readwrite'); tx.objectStore('videos').put(blob, id); tx.oncomplete = () => resolve(); tx.onerror = () => reject(tx.error); });
}
export async function getVideo(id: string): Promise<Blob | undefined> {
  const d = await db();
  return new Promise((resolve, reject) => { const r = d.transaction('videos').objectStore('videos').get(id); r.onsuccess = () => resolve(r.result as Blob | undefined); r.onerror = () => reject(r.error); });
}
export async function deleteVideo(id: string) {
  const d = await db();
  await new Promise<void>(resolve => { const tx = d.transaction('videos', 'readwrite'); tx.objectStore('videos').delete(id); tx.oncomplete = () => resolve(); tx.onerror = () => resolve(); });
}
