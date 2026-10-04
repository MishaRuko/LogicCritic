'use client';
import dynamic from 'next/dynamic';
// Trial's app reads localStorage, IndexedDB and the window size on first render, so it runs in the browser only.
const ExperimentApp = dynamic(() => import('../../components/experiment/ExperimentApp').then(m => m.ExperimentApp), { ssr: false });
export default function Page() { return <ExperimentApp/>; }
