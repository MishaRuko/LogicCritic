import * as api from './api';
export async function loadDemo(onCreated: (id: string) => void) {
  const response = await fetch('/demo/initial.md');
  if (!response.ok) throw new Error('The synthetic demo document could not be loaded.');
  const text = await response.text();
  const workspace = await api.createWorkspace('Sample study · Colour measurement reproducibility');
  onCreated(workspace.id);
  const source = await api.uploadSource(workspace.id, new File([text], 'sample-colour-study.md', { type: 'text/markdown' }));
  const evidence = source.excerpts.find(e => e.text.includes('higher absorbance'));
  const limitation = source.excerpts.find(e => e.text.includes('does not establish'));
  if (!evidence || !limitation) throw new Error('Demo excerpts were not found in the uploaded source.');
  await api.patchGraph(workspace.id, [
    { op: 'create_statement', client_ref: 'sample_result', text: evidence.text, assertion_mode: 'reported', role: 'premise', excerpt_ids: [evidence.id], provenance: api.userProvenance },
    { op: 'create_statement', client_ref: 'scope_limit', text: limitation.text, assertion_mode: 'reported', role: 'premise', excerpt_ids: [limitation.id], provenance: api.userProvenance },
    { op: 'create_statement', client_ref: 'hypothesis', text: 'The observed colour difference motivates a replication using the documented procedure.', assertion_mode: 'hypothesis', role: 'conclusion', excerpt_ids: [], provenance: api.userProvenance },
    { op: 'create_reasoning_step', client_ref: 'step', premise_ids: ['sample_result', 'scope_limit'], conclusion_id: 'hypothesis', explanation: 'The small bench study reports a difference in absorbance. A replication must follow the same preparation and temperature settings before comparing results.', provenance: api.userProvenance },
    { op: 'create_statement', client_ref: 'overclaim', text: 'Synthetic overclaim: the preparation method always causes a higher absorbance.', assertion_mode: 'asserted', role: 'conclusion', excerpt_ids: [], provenance: api.userProvenance },
    { op: 'create_annotation', subject_type: 'statement', subject_id: 'overclaim', type: 'claim_strength', value: { value: 'causal' }, provenance: api.userProvenance },
  ]);
  return { workspace };
}

export const labSample = {
  title: 'DJI_08 · Cell preparation',
  protocol: '/demo/lsv/Splitting-cells-DJI-027.pdf',
  text: '/demo/lsv/Splitting-cells-DJI-027.md',
  video: '/demo/lsv/DJI_08-first-30s.mp4',
  observations: '/demo/lsv/DJI_08-first-30s.observations.jsonl',
};
export function isLabSample(filename: string) { return /^Splitting-cells-DJI-027\.(md|pdf|txt)$/i.test(filename); }
export async function loadLabSample(onCreated: (id: string) => void) {
  const response = await fetch(labSample.text);
  if (!response.ok) throw new Error('The sample protocol could not be loaded.');
  const workspace = await api.createWorkspace(labSample.title);
  onCreated(workspace.id);
  const source = await api.uploadSource(workspace.id, new File([await response.text()], 'Splitting-cells-DJI-027.md', { type: 'text/markdown' }));
  const procedure = source.excerpts.find(e => e.text.includes('Spray hands with 70% ethanol'));
  if (!procedure) throw new Error('The sample procedure could not be read.');
  await api.patchGraph(workspace.id, [
    { op: 'create_statement', client_ref: 'procedure', text: 'The documented procedure begins with hand disinfection, gathering reagents, and disinfecting materials before placing them in the hood.', assertion_mode: 'reported', role: 'premise', excerpt_ids: [procedure.id], provenance: api.userProvenance },
    { op: 'create_statement', client_ref: 'scope', text: 'The first 30 seconds of DJI_08 may provide evidence about preparation; a longer recording is needed to assess the complete procedure.', assertion_mode: 'hypothesis', role: 'conclusion', excerpt_ids: [], provenance: api.userProvenance },
    { op: 'create_reasoning_step', client_ref: 'preparation_check', premise_ids: ['procedure'], conclusion_id: 'scope', explanation: 'Compare visible preparation with the documented initial steps. The clip ends before the full 22-step procedure is completed.', provenance: api.userProvenance },
  ]);
  return { workspace };
}
