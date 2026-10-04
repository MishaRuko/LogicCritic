import { Button } from '@cloudflare/kumo';
import { ArrowLeftIcon, ArrowRightIcon } from '@phosphor-icons/react';
import { MODEL } from '../../lib/experiment/agent';
import { LONG_WAIT_SECONDS, MIN_CONFIDENCE, ORDER_TOLERANCE_SECONDS } from '../../lib/experiment/conformance';
import { StatusIcon } from './Status';

const steps = [
  { title: 'Read the methodology', where: 'Server → Claude', body: <>The paper or protocol (PDF or text) goes to Claude with a structured-output schema. It returns each bench step with its wording, the conditions a camera could confirm, and caveats video can never establish: sterility, exact volumes, temperature.</> },
  { title: 'Skim the recording', where: 'Browser', body: <>The browser seeks through the <code>&lt;video&gt;</code> and draws frames onto a canvas: roughly one every three seconds, 8 to 40 frames at 512 px. The video itself never leaves the machine; only these stills are sent.</> },
  { title: 'Let the agent look closer', where: 'Browser ⇄ Server ⇄ Claude', body: <>Claude gets the steps and the overview, then drives a loop with a <code>view_frames(start, end, count)</code> tool. Each call returns 2 to 8 sharper frames (768 px) from the window it asked for. It has up to 16 turns and a 48-frame budget.</> },
  { title: 'Report per step', where: 'Claude', body: <>The agent finishes with <code>submit_findings</code>: for each step, when it happened, the timestamps that show it, a result for every check (confirmed, contradicted or not visible), and a confidence.</> },
  { title: 'Apply the rules', where: 'Browser', body: <>Plain TypeScript, not the model, decides the verdict. The same findings always give the same verdict, and every verdict can be traced to a timestamp you can scrub to.</> },
  { title: 'Seal the record', where: 'Browser', body: <>The method, observations and verdicts are canonicalised and SHA-256 hashed into an experiment record. It can be downloaded, checked with <code>npm run verify-record</code>, or shared as a <code>/r/&lt;id&gt;</code> link stored in KV.</> },
];

const columns = [
  { title: 'Browser', sub: 'React + Vite', items: ['Frame sampler (video → canvas)', 'Agent loop runner', 'view_frames executor', 'Verdict rules', 'Record hashing (SHA-256)', 'IndexedDB videos · localStorage analyses'] },
  { title: 'LogicCritic server', sub: 'Holds the API key', items: ['POST /experiment-api/method', 'POST /experiment-api/agent-turn', 'Next.js route handlers'] },
  { title: 'Anthropic API', sub: MODEL, items: ['Structured extraction (PDF input)', 'Tool use: view_frames, submit_findings', 'Extended thinking'] },
];

const links = [
  [['protocol, frames, tool results', 'right'], ['records', 'right']],
  [['extraction + agent turns', 'right'], ['steps, tool calls, findings', 'left']],
] as const;

const clips = [
  ['DJI_10', '1:35', 'CRISPR delivery', 'none'],
  ['DJI_16', '0:25', 'CRISPR delivery', 'reagent 2 added to reagent 3 instead of the mixing tube'],
  ['DJI_08', '4:30', 'Splitting cells', 'no incubation after TrypLE'],
  ['DJI_17', '2:19', 'E. coli transformation', 'skipped ice step (5 s, hard to see)'],
  ['DJI_23', '2:21', 'Adding cytokine', 'tips not changed'],
];

export function HowItWorks({ onExample }: { onExample: () => void }) {
  return <div className="how-page">
    <div className="page-intro how-intro">
      <h1>How Trial checks an experiment</h1>
      <p>The paper supplies the method. The recording supplies the evidence. Trial keeps them separate and shows, step by step, where one supports, contradicts or cannot speak to the other.</p>
      <Button size="sm" variant="secondary" onClick={onExample}>Open the DJI_16 example</Button>
    </div>

    <section className="how-section">
      <h2>Step by step</h2>
      <ol className="how-steps">{steps.map((s, i) => <li key={s.title}>
        <span className="mono">{String(i + 1).padStart(2, '0')}</span>
        <div><div className="how-step-head"><strong>{s.title}</strong><span>{s.where}</span></div><p>{s.body}</p></div>
      </li>)}</ol>
    </section>

    <section className="how-section">
      <h2>Architecture</h2>
      <p className="how-lede">The API key lives only on the LogicCritic server. The agent loop runs in the browser, because that is where the video is: each turn goes through the server to Claude, and any <code>view_frames</code> call comes back to be answered from the local file.</p>
      <div className="arch">{columns.map((c, i) => <div key={c.title} className="arch-cell">
        <div className="arch-box">
          <strong>{c.title}</strong><span className="arch-sub">{c.sub}</span>
          <ul>{c.items.map(item => <li key={item}>{item}</li>)}</ul>
        </div>
        {i < links.length && <div className="arch-links">{links[i].map(([label, dir]) =>
          <div key={label} className="arch-link">{dir === 'left' && <ArrowLeftIcon size={13}/>}<span>{label}</span>{dir === 'right' && <ArrowRightIcon size={13}/>}</div>)}
        </div>}
      </div>)}</div>
    </section>

    <section className="how-section">
      <h2>How verdicts are decided</h2>
      <div className="how-rules">
        <div><StatusIcon status="verified" size={14}/><strong>Verified</strong><p>Every core check confirmed (the frames clearly show the step was done) and at least one timestamp of evidence. Details the recording could not show, like a door closing after the video ends, are noted on the step without changing the verdict. Checks only cover what a camera can clearly see; volumes, labels, counts and exact durations are listed as caveats instead.</p></div>
        <div><StatusIcon status="contradicted" size={14}/><strong>Contradicted</strong><p>Confidence at least {MIN_CONFIDENCE} and the agent saw a check being broken (wrong tube, wrong vessel), or the step started out of order: outside the longest run of steps placed in protocol order by more than {ORDER_TOLERANCE_SECONDS} s.</p></div>
        <div><StatusIcon status="skipped" size={14}/><strong>Skipped</strong><p>The agent watched the stretch where the step belongs and saw it not done, and the step is not a wait longer than {LONG_WAIT_SECONDS} s (long waits are routinely cut from recordings). Steps that look the same on camera, like three unlabelled reagent additions, are reported as “one of” the group. Inferred from absence, so weaker than a contradiction.</p></div>
        <div><StatusIcon status="unverifiable" size={14}/><strong>Unverifiable</strong><p>Everything else. Low confidence, out of shot, or a condition video cannot show. Missing footage alone is never reported as skipped.</p></div>
      </div>
    </section>

    <section className="how-section">
      <h2>Checking the checker</h2>
      <p className="how-lede">The samples come from <a href="https://huggingface.co/datasets/cong-lab/lsv" target="_blank" rel="noreferrer">LabSuperVision</a>, which publishes step timings and error labels for each clip. The agent only sees the protocol and the video, so those labels work as an answer key. <code>npm run eval:agent</code> runs the same pipeline on them and scores timing overlap and whether the labelled error was flagged on the right step. Five clips is a smoke test, not a benchmark.</p>
      <table className="how-table">
        <thead><tr><th>Clip</th><th>Length</th><th>Protocol</th><th>Labelled error</th></tr></thead>
        <tbody>{clips.map(([id, len, protocol, error]) => <tr key={id}><td className="mono">{id}</td><td className="mono">{len}</td><td>{protocol}</td><td>{error}</td></tr>)}</tbody>
      </table>
    </section>
  </div>;
}
