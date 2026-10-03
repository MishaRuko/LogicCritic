import assert from 'node:assert/strict';
const base = (process.env.APP_URL ?? 'http://localhost').replace(/\/$/, '');
const ids = [];
async function call(path, init = {}) {
  const response = await fetch(`${base}/api${path}`, init);
  const text = await response.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!response.ok) throw new Error(`${response.status} ${path}: ${JSON.stringify(data)}`);
  return data;
}
const post = (path, body) => call(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const provenance = { actor_type: 'user', actor_id: 'frontend-integration-test' };
try {
  assert.equal((await call('/health/ready')).status, 'ok');
  const workspace = await post('/workspaces', { title: 'Frontend integration check (temporary)' }); ids.push(workspace.id);
  const other = await post('/workspaces', { title: 'Isolation check (temporary)' }); ids.push(other.id);
  assert((await call('/workspaces')).some(w => w.id === workspace.id));
  const form = new FormData(); form.append('file', new Blob(['# Synthetic integration material\n\nThe synthetic mouse study reports an association.\n\nNo human participants were studied.'], { type: 'text/markdown' }), 'synthetic-check.md');
  const source = await call(`/workspaces/${workspace.id}/sources`, { method: 'POST', body: form });
  assert(source.excerpts.length > 0);
  assert.deepEqual(await call(`/sources/${source.id}/excerpts`), source.excerpts);
  const patchKey = crypto.randomUUID();
  const patch = { idempotency_key: patchKey, operations: [
    { op: 'create_statement', client_ref: 'premise', text: source.excerpts[0].text, assertion_mode: 'reported', role: 'premise', excerpt_ids: [source.excerpts[0].id], provenance },
    { op: 'create_statement', client_ref: 'hypothesis', text: 'A synthetic hypothesis warrants further study.', assertion_mode: 'hypothesis', role: 'conclusion', excerpt_ids: [], provenance },
    { op: 'create_reasoning_step', client_ref: 'step', premise_ids: ['premise'], conclusion_id: 'hypothesis', explanation: 'The reported association motivates a hypothesis, without establishing causation.', provenance },
    { op: 'create_statement', client_ref: 'overclaim', text: 'Synthetic unsupported human claim.', assertion_mode: 'asserted', role: 'conclusion', excerpt_ids: [], provenance },
    { op: 'create_relation', source_node_kind: 'statement', source_node_id: 'premise', relation: 'qualifies', target_node_kind: 'statement', target_node_id: 'hypothesis', metadata: { lifecycle: 'proposed', rationale: 'Synthetic scope qualification.', provenance } },
    { op: 'create_annotation', subject_type: 'statement', subject_id: 'overclaim', type: 'claim_strength', value: { value: 'causal' }, provenance },
  ] };
  const result = await post(`/workspaces/${workspace.id}/graph-patches`, patch);
  assert.equal((await post(`/workspaces/${workspace.id}/graph-patches`, patch)).patch_id, result.patch_id);
  const graph = await call(`/workspaces/${workspace.id}/graph`);
  assert.equal(graph.statements.length, 3); assert.equal(graph.reasoning_steps.length, 1); assert.equal(graph.relations.length, 1);
  assert.equal(graph.relations[0].metadata.lifecycle, 'proposed');
  assert.deepEqual(graph.reasoning_steps[0].premise_ids, [result.id_map.premise]);
  const verification = await post(`/workspaces/${workspace.id}/verify`, {});
  assert(verification.issues_opened > 0);
  const context = await call(`/workspaces/${workspace.id}/statements/${result.id_map.overclaim}/context`);
  assert(context.issues.some(i => i.rule_code === 'ungrounded_statement'));
  assert(context.issues.some(i => i.rule_code === 'causality_overclaim'));
  assert(context.obligations.some(o => o.status === 'open'));
  await post(`/workspaces/${workspace.id}/review`, { node_type: 'statement', node_id: result.id_map.premise, decision: 'accepted', idempotency_key: crypto.randomUUID(), provenance });
  assert.equal((await call(`/workspaces/${workspace.id}/graph`)).statements.find(s => s.id === result.id_map.premise).lifecycle, 'accepted');
  await post(`/sources/${source.id}/validity`, { status: 'invalidated', reason: 'Synthetic test of source invalidation.', idempotency_key: crypto.randomUUID(), provenance });
  await post(`/workspaces/${workspace.id}/verify`, {});
  assert((await call(`/workspaces/${workspace.id}/statements/${result.id_map.premise}/context`)).issues.some(i => i.rule_code === 'invalidated_source'));
  await post(`/sources/${source.id}/validity`, { status: 'valid', reason: 'End of synthetic invalidation test.', idempotency_key: crypto.randomUUID(), provenance });
  const isolated = await fetch(`${base}/api/workspaces/${other.id}/statements/${result.id_map.premise}/context`);
  assert.equal(isolated.status, 404);
  const rejected = await fetch(`${base}/api/workspaces/${other.id}/graph-patches`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ idempotency_key: crypto.randomUUID(), operations: [{ op: 'create_statement', client_ref: 'bad', text: 'Cross-workspace excerpt', assertion_mode: 'reported', excerpt_ids: [source.excerpts[0].id], provenance }] }) });
  assert.equal(rejected.status, 422);
  const job = await post(`/sources/${source.id}/extract`, { idempotency_key: crypto.randomUUID() });
  assert.equal(job.workspace_id, workspace.id);
  const cancelled = await call(`/extraction-jobs/${job.id}`, { method: 'DELETE' });
  assert(['cancelled', 'failed', 'succeeded'].includes(cancelled.status));
  console.log('Live API flow passed: uploads, excerpts, graph patches, idempotency, review, verification, source validity, extraction cancellation and workspace isolation.');
} finally {
  for (const id of ids.reverse()) await call(`/workspaces/${id}`, { method: 'DELETE' });
}
