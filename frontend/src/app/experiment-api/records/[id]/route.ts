import { fetchPublished } from '../../../../lib/experiment/server';
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) { return fetchPublished((await params).id, process.env); }
