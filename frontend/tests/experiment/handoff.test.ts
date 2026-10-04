import { beforeEach, describe, expect, it } from 'vitest';
import { experimentUrl, linkAnalysis, linkedExperiments, methodFromSource, readHandoff, workspaceUrl } from '../../src/lib/experiment/handoff';
import { cachedAnalysis, samples } from '../../src/lib/experiment/demo';
import type { Analysis } from '../../src/lib/experiment/store';

const fakeFetch = (routes: Record<string, unknown>, status = 200) => (async (url: string) => new Response(JSON.stringify(routes[url] ?? null), { status: routes[url] ? status : 404 })) as unknown as typeof fetch;

describe('Argument → experiment hand-off', () => {
  beforeEach(() => localStorage.clear());
  it('round-trips the workspace and source through the URL', () => {
    const url = experimentUrl({ workspace: 'w1', source: 's1' });
    expect(url).toBe('/experiment?workspace=w1&source=s1');
    expect(readHandoff(url.slice(url.indexOf('?')))).toEqual({ workspace: 'w1', source: 's1' });
    expect(readHandoff('?workspace=w1')).toBeUndefined();
    expect(workspaceUrl('w1')).toBe('/research?workspace=w1');
  });
  it('rebuilds the method text from the excerpts in order', async () => {
    const file = await methodFromSource('s1', fakeFetch({
      '/api/sources/s1': { original_filename: 'Mock_Transformation.md' },
      '/api/sources/s1/excerpts': [{ text: '2. Flick the tube.', sequence: 1 }, { text: '1. Add DNA.', sequence: 0 }]
    }));
    expect(file.name).toBe('Mock_Transformation.txt');
    // jsdom's File lacks .text(); browsers have it.
    const text = await new Promise<string>(resolve => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.readAsText(file); });
    expect(text).toBe('1. Add DNA.\n\n2. Flick the tube.');
  });
  it('explains a missing or empty source', async () => {
    await expect(methodFromSource('gone', fakeFetch({}))).rejects.toThrow('Could not load the workspace source (404)');
    await expect(methodFromSource('s1', fakeFetch({ '/api/sources/s1': { original_filename: 'x.md' }, '/api/sources/s1/excerpts': [] }))).rejects.toThrow('no text');
  });
  it('links finished analyses to their workspace and summarises the verdicts', () => {
    const sample = samples.map(cachedAnalysis).find(a => a?.run.id === 'DJI_17') as Analysis;
    const mine = { ...sample, id: 'a1' }, other = { ...sample, id: 'a2' };
    linkAnalysis('w1', 'a1'); linkAnalysis('w1', 'a1'); linkAnalysis('w2', 'a2');
    const [summary, ...rest] = linkedExperiments('w1', [mine, other]);
    expect(rest).toHaveLength(0);
    expect(summary.analysis.id).toBe('a1');
    expect(summary.counts.skipped).toBe(1);
    expect(summary.flagged.some(f => f.includes('(skipped)'))).toBe(true);
    expect(linkedExperiments('w3', [mine, other])).toEqual([]);
  });
});
