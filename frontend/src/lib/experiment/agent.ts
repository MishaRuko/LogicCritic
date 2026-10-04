import { z } from 'zod';
import type Anthropic from '@anthropic-ai/sdk';
import type { Absence, MethodContract, Observation } from './types';

/** Shared between the browser, the Worker and the evaluation script. No SDK runtime code lives here. */
type MessageParam = Anthropic.Beta.Messages.BetaMessageParam;
type ContentBlock = Anthropic.Beta.Messages.BetaContentBlock;
type ContentBlockParam = Anthropic.Beta.Messages.BetaContentBlockParam;

export const MODEL = 'claude-opus-5-5';

// ---------- Methodology extraction ----------

export const extractedMethodSchema = z.object({
  title: z.string(),
  summary: z.string(),
  steps: z.array(z.object({
    title: z.string(),
    description: z.string(),
    defining_action: z.string(),
    visual_group: z.string(),
    checks: z.array(z.string()),
    detail_checks: z.array(z.string()),
    caveats: z.array(z.string()),
    criticality: z.enum(['informational', 'important', 'critical']),
    category: z.enum(['identity', 'timing', 'action', 'ordering'])
  }))
});
export type ExtractedMethod = z.infer<typeof extractedMethodSchema>;

export const EXTRACTION_SYSTEM = `You turn scientific methodology into an execution checklist that can be verified against a video of someone performing it at the bench.

Read the supplied document (a paper, protocol or notes). Find the experimental procedure that a person physically carries out, and return it as ordered steps. Ignore background, results, discussion and analysis that happens away from the bench.

For each step:
- title: a short imperative label, at most six words ("Add reagent 2 to tube").
- description: the instruction as the document states it, keeping quantities, times and materials.
- defining_action: the one visible action that tells this step apart from the steps around it, as a camera would see it. Be specific about how look-alike actions differ: an addition draws liquid from a source container and dispenses it into the target; mixing works within one tube (pipetting up and down in it, flicking, inverting) without drawing from another container; cleaning is spraying or wiping, whichever is used.
- visual_group: if this step and the steps directly before or after it would look the same on camera (e.g. "add reagent 1", "add reagent 2", "add reagent 3" from unlabelled containers, one after another), give them the same short label ("reagent addition"); otherwise an empty string. Steps separated by a different action are told apart by it and are not grouped.
- checks: the core conditions that show the step was performed: its action, the vessel or apparatus it uses (recognisable by appearance or position), a visible change of state. One to three is typical.
- detail_checks: conditions about how well or how completely it was done, or what follows it: manner ("the plate is held level"), technique ("the tip is placed at the well edge"), follow-through ("the incubator door is closed afterwards"), and order relative to other steps. A step whose core checks pass counts as done even if these cannot be seen. Often none.
- Both kinds: only conditions a camera watching the bench could clearly confirm or refute. Write them at the level of purpose and accept equivalent visible means ("items are cleaned, by spraying or wiping, before entering the hood"; "the tube is heated in a water bath, heat block or thermal cycler"). Never claim something about every item ("each bottle is sprayed"): sampled frames cannot show that. If a step is a wait (e.g. "incubate 20 min"), the check is qualitative: the item is put in place and left there rather than moved on straight away.
- caveats: everything the document requires that video cannot clearly establish. Always put these here, never in checks:
  - volumes, amounts, concentrations or any measured quantity (pipette settings, graduations, liquid levels);
  - identity that depends on reading a label or knowing contents (which reagent, which cell line);
  - exact counts ("4-5 flicks", "pipette 10 times");
  - exact durations and temperatures ("for 5 seconds", "20 min", "42°C");
  - claims about every item (each, every, all of them);
  - sterility and anything else not visible.
- criticality: critical if an error would invalidate the experiment, important if it would likely change the result, informational otherwise.
- category: identity (right material or vessel), timing (durations), ordering (sequence), or action (the physical manipulation).

Keep the document's own step granularity where it has numbered steps. Do not invent steps the document does not describe.`;

/** Conditions this video method cannot clearly establish. A check about one is moved to the caveats whatever the model
 * wrote. Vessel sizes ("1.5 mL tube", "10 cm dish") identify a vessel and stay; qualitative timing ("left on ice") stays. */
const UNVERIFIABLE: [reason: string, pattern: RegExp][] = [
  ['Measured quantity', /\b(volume|amount|concentration|quantity|graduation|liquid level)s?\b|\b(about|approximately|roughly|~)\s*\d+(\.\d+)?\s*(µl|μl|ul|ml|l|mg|µg|μg|ng|g|mm|µm|μm|nm|m)\b|\d+(\.\d+)?\s*(µl|μl|ul|ml|mg|µg|μg|ng|g)\s+of\b/i],
  ['Label or contents', /\blabel(l?ed|s)?\b|\blegible\b/i],
  ['Exact count', /\b\d+(\s*(-|–|to)\s*\d+)?\s+(\w+\s+){0,2}(times|flicks|strokes|inversions|taps)\b|\bnumber of\b|\bcount(ed)?\b/i],
  ['Claim about every item', /\b(each|every|all)\s+(of\s+the\s+)?(bottles?|items?|materials?|reagents?|tubes?|containers?|surfaces?|wells?|dishes?|plates?)\b/i],
  ['Exact duration or temperature', /\b\d+(\.\d+)?\s*(-|–|to)?\s*\d*\s*(s|secs?|seconds?|mins?|minutes?|h|hrs?|hours?)\b|\d+(\.\d+)?\s*°\s*[cf]\b|\bexactly\b/i],
];
export function unverifiableReason(check: string): string | undefined { return UNVERIFIABLE.find(([, p]) => p.test(check))?.[0]; }

/** Look-alike groups only hold consecutive steps: steps split by a different action (ice, heat shock, ice) are told
 * apart by that action, and grouping them invites the agent to pad the count. Each consecutive run keeps a group of
 * its own; a run of one is no group. */
export function adjacentGroups(groups: string[]): (string | undefined)[] {
  const label = groups.map(g => g.trim());
  const runs = label.map((g, i) => g && (label[i - 1] === g || label[i + 1] === g) ? g : '');
  const seen = new Map<string, number>();
  return runs.map((g, i) => {
    if (!g) return undefined;
    if (runs[i - 1] !== g) seen.set(g, (seen.get(g) ?? 0) + 1);
    const n = seen.get(g)!;
    return n > 1 ? `${g} (${n})` : g;
  });
}

export function toContract(extracted: ExtractedMethod, source: string): MethodContract {
  const groups = adjacentGroups(extracted.steps.map(s => s.visual_group));
  return {
    schemaVersion: 1, title: extracted.title, version: '1.0', source, summary: extracted.summary,
    requirements: extracted.steps.map((s, i) => {
      const core = s.checks.filter(c => !unverifiableReason(c)), details = s.detail_checks.filter(c => !unverifiableReason(c));
      const moved = [...s.checks, ...s.detail_checks].flatMap(c => { const reason = unverifiableReason(c); return reason ? [`${reason}, not verifiable from video: ${c}`] : []; });
      return {
        id: `step-${String(i + 1).padStart(2, '0')}`, order: i + 1, title: s.title, description: s.description,
        definingAction: s.defining_action || undefined, visualGroup: groups[i],
        checks: [...core.length ? core : [`${s.title} is visibly performed`], ...details], details: details.length ? details : undefined, caveats: [...s.caveats, ...moved],
        criticality: s.criticality, category: s.category
      };
    })
  };
}

// ---------- Video agent ----------

export type Frame = { t: number; data: string };
export type AgentEvent =
  | { kind: 'overview'; frames: number; duration: number }
  | { kind: 'thinking'; text: string }
  | { kind: 'inspect'; start: number; end: number; count: number }
  | { kind: 'retry'; message: string }
  | { kind: 'done'; steps: number };

export const AGENT_SYSTEM = `You are a lab-execution reviewer. You compare a video of someone performing a procedure against the methodology they were meant to follow, and report what the recording shows for each step.

You receive the methodology (steps with numbered checks) and an overview of the recording: frames sampled evenly across it, each labelled with its timestamp. Use view_frames to look more closely at any window: it returns denser, higher-resolution frames. Fine detail (which tube a pipette tip enters, a label, a colour change) usually needs a closer look, so inspect each step's window before judging it. You have a limited frame budget, so target windows rather than re-scanning the whole video.

For each methodology step:
1. Locate it: the time window where it is performed, or found=false if you cannot locate it.
   A step is performed only when its defining action is seen: for "incubate on ice", the tube put into the ice and left there. Being near the apparatus is not doing the step: a tube held over an ice bucket while it is flicked, or carried past it to the next instrument, does not count.
   Steps often run together in one motion. Place each step only by its own defining action. If that action never happens between the neighbouring steps, the step is not found, even if the neighbours sit right next to each other.
   Steps can also interleave (gathering reagents while cleaning them); their windows may overlap.
   To judge a motion (rocking, tapping, flicking, swirling, pipetting up and down, inverting), request 6 to 8 frames over 2 to 4 seconds; single frames cannot show movement.
   If found=false, set absence: "not_performed" only if the recording continuously shows the stretch where this step belongs (between the neighbouring steps you located) and the step is clearly not done there; otherwise "not_visible" (it could have happened out of shot, between sampled frames, or in a part of the procedure the recording does not cover: recordings can be excerpts). Your confidence then rates this judgement. If found=true, set absence to "n/a".
2. Judge every check against what is visible:
   - confirmed: the frames clearly show it.
   - contradicted: the frames clearly show something incompatible (a different target vessel, a skipped addition in a window you can see, the wrong order).
   - not_visible: out of frame, too small, occluded, or not establishable from sampled frames (e.g. a duration).
   Only identify a material or vessel when the video makes it unambiguous (legible label, a position established earlier in the recording). Otherwise say not_visible and explain.
3. Cite evidence: timestamps of the frames that support your judgement, with a one-sentence description of what is visible there.
4. Give a confidence between 0 and 1 for your localisation and judgements together.

Absence of footage is not evidence of omission. Describe what you see; do not assume the person followed the method.

When you have assessed every step, call submit_findings once with all steps.`;

export const AGENT_TOOLS: Anthropic.Beta.Messages.BetaTool[] = [
  {
    name: 'view_frames',
    description: 'Return evenly spaced, higher-resolution frames from a window of the recording, each labelled with its timestamp. Use it to examine a step closely. Windows of 2–20 seconds work best.',
    strict: true,
    input_schema: {
      type: 'object', additionalProperties: false, required: ['start_seconds', 'end_seconds', 'count'],
      properties: {
        start_seconds: { type: 'number', description: 'Window start in seconds.' },
        end_seconds: { type: 'number', description: 'Window end in seconds.' },
        count: { type: 'integer', description: 'Number of frames, 2 to 8.' }
      }
    }
  },
  {
    name: 'submit_findings',
    description: 'Submit the final assessment for every methodology step. Call once, after inspecting the recording.',
    strict: true,
    input_schema: {
      type: 'object', additionalProperties: false, required: ['steps'],
      properties: {
        steps: {
          type: 'array',
          items: {
            type: 'object', additionalProperties: false,
            required: ['step_id', 'found', 'absence', 'start_seconds', 'end_seconds', 'summary', 'check_results', 'evidence', 'uncertainties', 'confidence'],
            properties: {
              step_id: { type: 'string' },
              found: { type: 'boolean', description: 'Whether the step could be located in the recording.' },
              absence: { type: 'string', enum: ['n/a', 'not_performed', 'not_visible'], description: 'When found is false: not_performed if the stretch where the step belongs is visible and it is clearly not done there, else not_visible. n/a when found.' },
              start_seconds: { type: 'number', description: '0 if not found.' },
              end_seconds: { type: 'number', description: '0 if not found.' },
              summary: { type: 'string', description: 'What the recording shows for this step, in one or two sentences.' },
              check_results: {
                type: 'array',
                items: {
                  type: 'object', additionalProperties: false, required: ['check_index', 'result', 'note'],
                  properties: {
                    check_index: { type: 'integer' },
                    result: { type: 'string', enum: ['confirmed', 'contradicted', 'not_visible'] },
                    note: { type: 'string', description: 'What is visible that supports this result.' }
                  }
                }
              },
              evidence: {
                type: 'array',
                items: {
                  type: 'object', additionalProperties: false, required: ['seconds', 'description'],
                  properties: { seconds: { type: 'number' }, description: { type: 'string' } }
                }
              },
              uncertainties: { type: 'array', items: { type: 'string' } },
              confidence: { type: 'number' }
            }
          }
        }
      }
    }
  }
];

const findingsSchema = z.object({
  steps: z.array(z.object({
    step_id: z.string(), found: z.boolean(), absence: z.enum(['n/a', 'not_performed', 'not_visible']), start_seconds: z.number(), end_seconds: z.number(), summary: z.string(),
    check_results: z.array(z.object({ check_index: z.number().int(), result: z.enum(['confirmed', 'contradicted', 'not_visible']), note: z.string() })),
    evidence: z.array(z.object({ seconds: z.number(), description: z.string() })),
    uncertainties: z.array(z.string()), confidence: z.number().min(0).max(1)
  }))
});
type Findings = z.infer<typeof findingsSchema>;

export function clock(seconds: number): string {
  const s = Math.max(0, seconds);
  return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toFixed(1).padStart(4, '0')}`;
}

function methodBrief(method: MethodContract): string {
  return method.requirements.map(r => [
    `${r.id} — ${r.title}`,
    `  Method: ${r.description}`,
    r.definingAction ? `  Defining action: ${r.definingAction}` : '',
    '  Checks:', ...(r.checks ?? []).map((c, i) => `    [${i}] ${c}`),
    r.caveats?.length ? `  Not establishable from video: ${r.caveats.join('; ')}` : ''
  ].filter(Boolean).join('\n')).join('\n\n');
}

function frameBlocks(frames: Frame[]): (Anthropic.Beta.Messages.BetaTextBlockParam | Anthropic.Beta.Messages.BetaImageBlockParam)[] {
  return frames.flatMap(f => [
    { type: 'text' as const, text: `Frame at ${clock(f.t)} (${f.t.toFixed(1)} s)` },
    { type: 'image' as const, source: { type: 'base64' as const, media_type: 'image/jpeg' as const, data: f.data } }
  ]);
}

export type AgentCall = (messages: MessageParam[]) => Promise<{ content: ContentBlock[]; stop_reason: string | null }>;
export type AgentOptions = {
  method: MethodContract; duration: number; overview: Frame[];
  getFrames: (start: number, end: number, count: number) => Promise<Frame[]>;
  call: AgentCall; onEvent?: (event: AgentEvent) => void;
  maxTurns?: number; frameBudget?: number;
};

export type AgentResult = { observations: Observation[]; absences: Absence[] };

/** Runs the inspection loop: the model asks for frames, the caller supplies them, until findings are submitted. */
export async function runAgent({ method, duration, overview, getFrames, call, onEvent, maxTurns = 20, frameBudget = Math.max(48, 4 * method.requirements.length) }: AgentOptions): Promise<AgentResult> {
  let budget = frameBudget;
  const messages: MessageParam[] = [{
    role: 'user',
    content: [
      { type: 'text', text: `Methodology: ${method.title}\n\n${methodBrief(method)}\n\nThe recording is ${clock(duration)} long (${duration.toFixed(1)} s). Overview frames follow.` },
      ...frameBlocks(overview),
      { type: 'text', text: `You may request up to ${frameBudget} more frames with view_frames. Assess every step, then call submit_findings.` }
    ]
  }];
  onEvent?.({ kind: 'overview', frames: overview.length, duration });
  for (let turn = 0; turn < maxTurns; turn++) {
    const response = await call(messages);
    messages.push({ role: 'assistant', content: response.content as ContentBlockParam[] });
    for (const block of response.content) if (block.type === 'thinking' && block.thinking.trim()) onEvent?.({ kind: 'thinking', text: block.thinking.trim() });
    if (response.stop_reason === 'refusal') throw new Error('The model declined to analyse this recording.');
    if (response.stop_reason === 'max_tokens') throw new Error('The model ran out of output space before finishing.');
    const uses = response.content.filter((b): b is Anthropic.Beta.Messages.BetaToolUseBlock => b.type === 'tool_use');
    if (!uses.length) { messages.push({ role: 'user', content: 'Call submit_findings with your assessment of every step.' }); continue; }
    const results: ContentBlockParam[] = [];
    let findings: Findings | undefined;
    for (const use of uses) {
      if (use.name === 'view_frames') {
        const input = use.input as { start_seconds: number; end_seconds: number; count: number };
        const start = Math.max(0, Math.min(duration, input.start_seconds)), end = Math.max(start, Math.min(duration, input.end_seconds));
        const count = Math.max(0, Math.min(8, Math.round(input.count), budget));
        if (!count) { results.push({ type: 'tool_result', tool_use_id: use.id, is_error: true, content: 'Frame budget exhausted. Submit your findings now.' }); continue; }
        budget -= count;
        onEvent?.({ kind: 'inspect', start, end, count });
        const frames = await getFrames(start, end, count);
        results.push({ type: 'tool_result', tool_use_id: use.id, content: [...frameBlocks(frames), { type: 'text', text: `${budget} frames left in your budget.` }] });
      } else if (use.name === 'submit_findings') {
        const parsed = findingsSchema.safeParse(use.input);
        const missing = parsed.success ? method.requirements.filter(r => !parsed.data.steps.some(s => s.step_id === r.id)).map(r => r.id) : [];
        if (!parsed.success) results.push({ type: 'tool_result', tool_use_id: use.id, is_error: true, content: `Findings did not match the schema: ${parsed.error.message}` });
        else if (missing.length) results.push({ type: 'tool_result', tool_use_id: use.id, is_error: true, content: `Include every step. Missing: ${missing.join(', ')}.` });
        else { findings = parsed.data; results.push({ type: 'tool_result', tool_use_id: use.id, content: 'Findings received.' }); }
        if (!findings) onEvent?.({ kind: 'retry', message: 'Findings were incomplete; asking the agent to resubmit.' });
      } else results.push({ type: 'tool_result', tool_use_id: use.id, is_error: true, content: `Unknown tool ${use.name}.` });
    }
    if (findings) { onEvent?.({ kind: 'done', steps: findings.steps.length }); return { observations: toObservations(method, findings, duration), absences: toAbsences(method, findings) }; }
    messages.push({ role: 'user', content: results });
  }
  throw new Error('The agent did not submit findings within its turn limit.');
}

function toAbsences(method: MethodContract, findings: Findings): Absence[] {
  return findings.steps.flatMap(s => !s.found && s.absence !== 'n/a' && method.requirements.some(r => r.id === s.step_id)
    ? [{ stepId: s.step_id, reason: s.absence, confidence: s.confidence, note: s.summary }] : []);
}

function toObservations(method: MethodContract, findings: Findings, duration: number): Observation[] {
  return findings.steps.flatMap(s => {
    const requirement = method.requirements.find(r => r.id === s.step_id);
    if (!requirement || !s.found) return [];
    const start = Math.max(0, Math.min(duration, s.start_seconds)), end = Math.max(start + 0.5, Math.min(duration, s.end_seconds));
    const checks = requirement.checks ?? [];
    return [{
      id: `${s.step_id}-agent`, stepId: s.step_id, timestampStart: start, timestampEnd: end, observed: {}, establishes: [],
      uncertain: [...s.uncertainties, ...(requirement.caveats ?? []).map(c => `Not establishable from video: ${c}`)],
      confidence: s.confidence, provenance: 'claude_agent' as const, summary: s.summary,
      checkResults: checks.map((check, i) => {
        const r = s.check_results.find(c => c.check_index === i);
        return r ? { check, result: r.result, note: r.note } : { check, result: 'not_visible' as const, note: 'Not assessed.' };
      }),
      evidence: (s.evidence.length ? s.evidence : [{ seconds: start, description: s.summary }]).map((e, i) => ({
        id: `${s.step_id}-e${i}`, timestamp: Math.max(0, Math.min(duration, e.seconds)), end: Math.min(duration, e.seconds + 1), description: e.description, kind: 'video' as const
      }))
    }];
  });
}
