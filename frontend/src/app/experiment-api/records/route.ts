import { publish } from '../../../lib/experiment/server';
export function POST(request: Request) { return publish(request, process.env); }
