import { verifyRun } from './conformance';
import { loadAnalyses, type Analysis } from './store';
import type { Status } from './types';

/**
 * The optional hand-off between the two checks. Both live on one origin, so the argument workspace passes only ids in
 * the URL; the experiment page reads the source text from the API, and finished analyses are linked back per workspace
 * in this browser (like the workspace's own source and job registry).
 */
export type Handoff = { workspace: string; source: string };
const LINKS = 'experiment-workspace-links-v1';

export const experimentUrl = ({ workspace, source }: Handoff) => `/experiment?${new URLSearchParams({ workspace, source })}`;
export const workspaceUrl = (workspace: string) => `/research?${new URLSearchParams({ workspace })}`;

export function readHandoff(search: string): Handoff | undefined {
  const params = new URLSearchParams(search);
  const workspace = params.get('workspace'), source = params.get('source');
  return workspace && source ? { workspace, source } : undefined;
}

type Excerpt = { text: string; sequence: number };
/** The source's text, rebuilt from its excerpts in order, as a file New analysis can read like an upload. */
export async function methodFromSource(source: string, fetcher: typeof fetch = fetch): Promise<File> {
  const get = async <T>(path: string): Promise<T> => {
    const response = await fetcher(`/api${path}`);
    if (!response.ok) throw new Error(`Could not load the workspace source (${response.status}).`);
    return response.json() as Promise<T>;
  };
  const [meta, excerpts] = await Promise.all([get<{ original_filename: string }>(`/sources/${source}`), get<Excerpt[]>(`/sources/${source}/excerpts`)]);
  if (!excerpts.length) throw new Error('That source has no text to use as a method.');
  const text = [...excerpts].sort((a, b) => a.sequence - b.sequence).map(e => e.text).join('\n\n');
  return new File([text], meta.original_filename.replace(/\.[^.]+$/, '') + '.txt', { type: 'text/plain' });
}

function readLinks(): Record<string, string[]> { try { return JSON.parse(localStorage.getItem(LINKS) ?? '{}'); } catch { return {}; } }
export function linkAnalysis(workspace: string, analysisId: string) {
  const links = readLinks();
  links[workspace] = [...new Set([...(links[workspace] ?? []), analysisId])];
  localStorage.setItem(LINKS, JSON.stringify(links));
}

export type ExperimentSummary = { analysis: Analysis; counts: Record<Status, number>; flagged: string[]; detailNotes: number };
/** Verdict counts for the experiments run from this workspace, newest first; analyses deleted since are skipped. */
export function linkedExperiments(workspace: string, analyses: Analysis[] = loadAnalyses()): ExperimentSummary[] {
  const ids = readLinks()[workspace] ?? [];
  return analyses.filter(a => ids.includes(a.id)).reverse().map(analysis => {
    const results = verifyRun(analysis.method, analysis.run);
    const counts: Record<Status, number> = { verified: 0, contradicted: 0, skipped: 0, unverifiable: 0 };
    for (const r of Object.values(results)) counts[r.status]++;
    const flagged = analysis.method.requirements.filter(r => ['skipped', 'contradicted'].includes(results[r.id]?.status)).map(r => `${r.order}. ${r.title} (${results[r.id].status})`);
    const detailNotes = Object.values(results).filter(r => r.status === 'verified' && r.unconfirmedDetails?.length).length;
    return { analysis, counts, flagged, detailNotes };
  });
}
