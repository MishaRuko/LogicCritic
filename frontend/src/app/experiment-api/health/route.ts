import { health } from '../../../lib/experiment/server';
export const dynamic = 'force-dynamic';
export function GET() { return health(process.env); }
