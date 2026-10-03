import * as api from './api';
export async function loadDemo(onCreated: (id: string) => void) {
  const response = await fetch('/demo/initial.md');
  if (!response.ok) throw new Error('The synthetic demo document could not be loaded.');
  const text = await response.text();
  const workspace = await api.createWorkspace('Synthetic mouse-study argument');
  onCreated(workspace.id);
  const source = await api.uploadSource(workspace.id, new File([text], 'synthetic-mouse-study.md', { type: 'text/markdown' }));
  const evidence = source.excerpts.find(e => e.text.includes('completed the maze faster'));
  const limitation = source.excerpts.find(e => e.text.includes('does not establish'));
  if (!evidence || !limitation) throw new Error('Demo excerpts were not found in the uploaded source.');
  await api.patchGraph(workspace.id, [
    { op: 'create_statement', client_ref: 'mouse_result', text: evidence.text, assertion_mode: 'reported', role: 'premise', excerpt_ids: [evidence.id], provenance: api.userProvenance },
    { op: 'create_statement', client_ref: 'scope_limit', text: limitation.text, assertion_mode: 'reported', role: 'premise', excerpt_ids: [limitation.id], provenance: api.userProvenance },
    { op: 'create_statement', client_ref: 'hypothesis', text: 'The mouse association is a hypothesis for further investigation, subject to the stated limitations.', assertion_mode: 'hypothesis', role: 'conclusion', excerpt_ids: [], provenance: api.userProvenance },
    { op: 'create_reasoning_step', client_ref: 'step', premise_ids: ['mouse_result', 'scope_limit'], conclusion_id: 'hypothesis', explanation: 'The synthetic mouse result motivates further investigation; the source explicitly limits any interpretation about people or causation.', provenance: api.userProvenance },
    { op: 'create_statement', client_ref: 'overclaim', text: 'Synthetic overclaim: the intervention causes improved memory in people.', assertion_mode: 'asserted', role: 'conclusion', excerpt_ids: [], provenance: api.userProvenance },
    { op: 'create_annotation', subject_type: 'statement', subject_id: 'overclaim', type: 'claim_strength', value: { value: 'causal' }, provenance: api.userProvenance },
  ]);
  return { workspace, verification: await api.verify(workspace.id) };
}
