'use client';
import { use } from 'react';
import dynamic from 'next/dynamic';
// Public, read-only view of a published record (Trial's /r/<id>), browser-only like the rest of the experiment app.
const SharedRecordPage = dynamic(() => import('../../../../components/experiment/ExperimentApp').then(m => m.SharedRecordPage), { ssr: false });
export default function Page({ params }: { params: Promise<{ id: string }> }) { return <SharedRecordPage id={use(params).id}/>; }
