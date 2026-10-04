import { SharedRecord } from '../../../components/experiment/SharedRecord';
export default async function RecordPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <div className="experiment-app px-5 sm:px-9"><SharedRecord id={id}/></div>;
}
