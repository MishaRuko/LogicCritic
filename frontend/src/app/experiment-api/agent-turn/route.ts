import { turn } from '../../../lib/experiment/server';
// An agent turn streams one long Claude response (adaptive thinking, up to 32k tokens).
export const maxDuration = 300;
export function POST(request: Request) { return turn(request, process.env); }
