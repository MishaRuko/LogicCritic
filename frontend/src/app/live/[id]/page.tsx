import { LiveCamera } from '../../../components/experiment/LiveCamera';

export default async function LivePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <LiveCamera runId={id} />;
}
