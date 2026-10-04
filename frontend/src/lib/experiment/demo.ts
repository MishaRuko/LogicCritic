import methodJSON from './fixtures/method.json';
import source from './fixtures/source.json';
import observations from './fixtures/observations.json';
import assets from './fixtures/assets.json';
import agentRuns from './fixtures/agent-runs.json';
import { compileMethod } from './protocol';
import type { Absence, MethodContract, Observation, Run } from './types';
import type { Analysis } from './store';

/** The original hand-built contract for the CRISPR clips, used only by the annotation-assisted fixtures. */
export const demoMethod = compileMethod(JSON.stringify(methodJSON));
export const demoSource = source;
type CachedAgentRun = { method: MethodContract; observations: Observation[]; absences?: Absence[]; log: string[]; generatedAt: string };

export type Sample = {
  id: string; title: string; deviation: string; duration: number; date: string; video: string; poster: string;
  protocolName: string; protocolText: string; protocolPdf: string; groundTruth: { steps: { order: number; text: string; start: number | null; end: number | null }[]; error: string };
  run: Run;
};
type Example = (typeof source.examples)[number];
type Asset = (typeof assets)[number];
/** Builds a sample from a manifest row. Exported so the evaluation can load held-out rows the app never bundles. */
export function toSample(e: Example, revision: string, assetList: Asset[] = assets): Sample {
  const slug = e.protocol.replace(/\.txt$/, '').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const asset = assetList.find(a => a.clip === e.clip_id);
  const protocol = JSON.parse(e.protocol_json) as { steps: { order: number; text: string; t_start: number | null; t_end: number | null }[] };
  return {
    id: e.clip_id, title: e.protocol_title, deviation: e.has_error ? e.error : 'No documented error', duration: e.duration_s, date: e.date,
    video: e.cached_path, poster: `/experiment/demo/${e.clip_id}-0.jpg`, protocolName: `${slug}.pdf`, protocolText: `/experiment/demo/protocols/${slug}.txt`, protocolPdf: `/experiment/demo/protocols/${slug}.pdf`,
    groundTruth: { error: e.has_error ? e.error : '', steps: protocol.steps.map(s => ({ order: s.order, text: s.text, start: s.t_start, end: s.t_end })) },
    run: {
      id: e.clip_id, sourceClip: e.slice_id, title: e.protocol_title, subtitle: e.has_error ? 'Documented deviation' : 'Reference execution',
      date: e.date, video: e.cached_path, duration: e.duration_s, poster: `/experiment/demo/${e.clip_id}-0.jpg`, observations: [],
      mediaProvenance: asset && { sourceRevision: revision, sourceSha256: e.source_sha256, cachedSha256: asset.cachedSha256, sourceUrl: `${source.source}/resolve/${source.revision}/${e.video_640_path}`, originalFilename: e.video_name, transform: asset.transform }
    }
  };
}
export const samples: Sample[] = source.examples.map(e => toSample(e, source.revision));

/** A ready-to-open analysis for a sample: a cached real agent run if one exists, otherwise the annotation-assisted fixture. */
export function cachedAnalysis(sample: Sample): Analysis | undefined {
  const agent = (agentRuns as Record<string, CachedAgentRun>)[sample.id];
  const base = { id: `sample-${sample.id}`, createdAt: agent?.generatedAt ?? '2026-10-03T00:00:00Z', methodName: sample.protocolName, videoName: `${sample.id}.mp4` };
  if (agent) return { ...base, mode: 'agent', method: agent.method, log: agent.log, run: { ...sample.run, observations: agent.observations, absences: agent.absences } };
  const fixture = (observations as Record<string, Observation[]>)[sample.id];
  if (fixture) return { ...base, mode: 'annotation', method: demoMethod, run: { ...sample.run, observations: fixture } };
  return undefined;
}

export function time(seconds: number): string { const s = Math.max(0, Math.floor(seconds)); return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`; }
export function readable(value: string): string { return ({ mixing_tube: 'Mixing tube', reagent_1: 'Reagent 1', reagent_2: 'Reagent 2', reagent_3: 'Reagent 3', culture_dish: 'Culture dish', pipette_transfer: 'Pipette transfer', dropwise_transfer: 'Dropwise transfer', rock_dish: 'Dish rocking', mix: 'Mixing', sterility: 'Tube sterility', cell_identity: '293T cell identity', confluency: '70–80% confluency', continuous_recording: 'Continuous incubation recording', room_temperature: 'Room temperature', action: 'Transfer action', source: 'Source identity', target: 'Target identity' } as Record<string, string>)[value] ?? value.replaceAll('_', ' '); }
