import { extract } from '../../../lib/experiment/server';
export function POST(request: Request) { return extract(request, process.env); }
