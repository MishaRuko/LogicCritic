'use client';

import { useEffect, useState } from 'react';
import { Empty, Loader } from '@cloudflare/kumo';
import { CheckIcon, WarningIcon } from '@phosphor-icons/react';
import { fetchRecord, validateRecord } from '../../lib/experiment/record';
import type { ExperimentRecord } from '../../lib/experiment/types';
import { DownloadMenu, RecordView } from './RecordView';

/** Public, read-only page for a published record at /r/<id>. Hashes are recomputed in the viewer's browser. */
export function SharedRecord({ id }: { id: string }) {
  const [record, setRecord] = useState<ExperimentRecord>();
  const [intact, setIntact] = useState<boolean>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    fetchRecord(id).then(async r => { setRecord(r); document.title = `${r.method.title} · Trial record`; setIntact(await validateRecord(r)); }, e => setError(e instanceof Error ? e.message : String(e)));
  }, [id]);

  return <div className="shared-page">
    <header className="shared-header">
      <a className="sidebar-brand" href="/"><strong>Trial</strong></a>
      {record && <div className="shared-actions">
        <span className={`integrity ${intact === false ? 'contradicted' : ''}`}>
          {intact === undefined ? 'Checking hashes…' : intact ? <><CheckIcon size={13} weight="bold"/>Hashes verified in your browser</> : <><WarningIcon size={13} weight="bold"/>Hashes do not match this record</>}
        </span>
        <DownloadMenu record={record}/>
      </div>}
    </header>
    <main className="shared-main">
      {error ? <Empty title="Record unavailable" description={error}/>
        : record ? <RecordView record={record} shareUrl={location.href}/>
        : <div className="shared-loading"><Loader/></div>}
    </main>
  </div>;
}
