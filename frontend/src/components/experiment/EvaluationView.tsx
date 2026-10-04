import { Empty, Link, Table } from '@cloudflare/kumo';
import { ShieldCheckIcon } from '@phosphor-icons/react';
import evaluation from '../../lib/experiment/fixtures/agent-eval.json';
import { demoSource, samples, time } from '../../lib/experiment/demo';
type Clip = { title: string; duration: number; split?: 'dev' | 'heldout'; labelledError: string | null; predictedError: boolean; exact?: boolean; trueSkips?: number[]; eitherSteps?: number[]; errorSteps?: number[]; skipsCaught?: number[]; errorsCaught?: number[]; falseAlarms?: number[]; counts: Record<string, number>; contradictions: { step: number; title: string; status?: string; observed: unknown }[]; localisation: string | { steps: number; meanIoU: number; hitsAtIoU03: number } };
export function EvaluationView() {
  const clips = Object.entries((evaluation as { clips: Record<string, Clip> }).clips);
  const labelled = clips.filter(([, c]) => c.labelledError), clean = clips.filter(([, c]) => !c.labelledError);
  const splits = (['dev', 'heldout'] as const).map(split => [split, clips.filter(([, c]) => c.split === split).map(([, c]) => c)] as const).filter(([, cs]) => cs.length);
  const total = (cs: readonly Clip[], f: (c: Clip) => number) => cs.reduce((n, c) => n + f(c), 0);
  const located = clips.map(([, c]) => c.localisation).filter((l): l is Exclude<Clip['localisation'], string> => typeof l === 'object');
  return <div className="evaluation-page">
    <div className="page-intro"><h1>Does it work?</h1><p>The samples come from LabSuperVision, which publishes when each protocol step happens and what went wrong. The agent never sees those labels, so they are an answer key.</p></div>
    {clips.length ? <>
      <div className="metrics">
        <div><span>Labelled errors flagged</span><strong>{labelled.filter(([, c]) => c.predictedError).length}<small> / {labelled.length}</small></strong></div>
        <div><span>False alarms on clean clips</span><strong>{clean.filter(([, c]) => c.predictedError).length}<small> / {clean.length}</small></strong></div>
        <div><span>Steps located (IoU ≥ 0.3)</span><strong>{located.reduce((n, l) => n + l.hitsAtIoU03, 0)}<small> / {located.reduce((n, l) => n + l.steps, 0)}</small></strong></div>
        <div><span>Mean timing overlap</span><strong>{located.length ? (located.reduce((n, l) => n + l.meanIoU * l.steps, 0) / Math.max(1, located.reduce((n, l) => n + l.steps, 0))).toFixed(2) : '—'}<small> IoU</small></strong></div>
      </div>
      {splits.length > 0 && <Table className="eval-table"><Table.Header><Table.Row><Table.Head>Split</Table.Head><Table.Head>Exactly right</Table.Head><Table.Head>Skips caught</Table.Head><Table.Head>Errors on the right step</Table.Head><Table.Head>False alarms</Table.Head></Table.Row></Table.Header><Table.Body>{splits.map(([split, cs]) => <Table.Row key={split}>
        <Table.Cell><strong>{split === 'dev' ? 'Development' : 'Held-out'}</strong><span className="muted">{split === 'dev' ? ' tuned on' : ' run once after the design lock'}</span></Table.Cell>
        <Table.Cell className="mono">{cs.filter(c => c.exact).length} / {cs.length}</Table.Cell>
        <Table.Cell className="mono">{total(cs, c => c.skipsCaught?.length ?? 0)} / {total(cs, c => (c.trueSkips?.length ?? 0) + (c.eitherSteps?.length ?? 0))}</Table.Cell>
        <Table.Cell className="mono">{total(cs, c => c.errorsCaught?.length ?? 0)} / {total(cs, c => c.errorSteps?.length ?? 0)}</Table.Cell>
        <Table.Cell className="mono">{total(cs, c => c.falseAlarms?.length ?? 0)}</Table.Cell>
      </Table.Row>)}</Table.Body></Table>}
      <Table className="eval-table"><Table.Header><Table.Row><Table.Head>Clip</Table.Head><Table.Head>Labelled error</Table.Head><Table.Head>Agent flagged</Table.Head><Table.Head>Timing</Table.Head></Table.Row></Table.Header><Table.Body>{clips.map(([id, c]) => <Table.Row key={id}>
        <Table.Cell><strong>{c.title}</strong><span className="mono muted">{id} · {time(c.duration)}{c.split ? ` · ${c.split}` : ''}</span></Table.Cell>
        <Table.Cell>{c.labelledError ?? <span className="muted">None</span>}</Table.Cell>
        <Table.Cell>{c.contradictions.length ? c.contradictions.map(x => <div key={x.step}>Step {x.step} · {x.title}{x.status === 'skipped' ? ' (skipped)' : ''}{c.falseAlarms?.includes(x.step) ? <strong className="contradicted"> · false alarm</strong> : null}<span className="muted"> — {String(x.observed)}</span></div>) : <span className="muted">Nothing</span>}</Table.Cell>
        <Table.Cell className="mono">{typeof c.localisation === 'object' ? `${c.localisation.hitsAtIoU03}/${c.localisation.steps} · IoU ${c.localisation.meanIoU.toFixed(2)}` : '—'}</Table.Cell>
      </Table.Row>)}</Table.Body></Table>
      <p className="muted small">Read the flagged step against the labelled error: a contradiction on the wrong step is not a detection. {clips.length} clips is a smoke test, not a benchmark.</p>
    </> : <Empty icon={<ShieldCheckIcon size={28}/>} title="No agent runs yet" description="Add CLAUDE_API_KEY to LogicCritic/.env, then run the pipeline on every sample and score it against the published labels. Results appear here and the samples open with the agent’s real output." commandLine="npm run eval:agent"/>}
    <section className="dataset"><h2>Dataset</h2><p>LabSuperVision / LabOS LSV · CC BY-NC 4.0 · {samples.length} clips used · pinned revision <code>{demoSource.revision.slice(0, 8)}</code></p><Link href={demoSource.source} target="_blank">Dataset on Hugging Face</Link></section>
  </div>;
}
