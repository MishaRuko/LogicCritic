'use client';

import { useEffect, useState } from 'react';

export function RecordingPreview({ file, src, filename }: { file?: File; src?: string; filename?: string }) {
  const [localUrl, setLocalUrl] = useState<string>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    setLocalUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const url = file ? localUrl : src;
  useEffect(() => { setFailed(false); }, [url]);
  return <section aria-label="Recording preview" className="my-5">
    <p className="mb-3! text-xs text-zinc-500">{file?.name ?? filename}</p>
    {url && <video key={url} controls preload="metadata" src={url} aria-label="Lab video preview" onError={() => setFailed(true)} onLoadedMetadata={() => setFailed(false)} className="w-full max-h-[45vh] bg-zinc-900 object-contain"/>}
    {failed && <p role="status" className="mt-3! text-xs text-zinc-500">This browser cannot preview this video format. You can still upload it for analysis.</p>}
  </section>;
}
