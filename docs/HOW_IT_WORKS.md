# How Trial works

This is the plain account of what the system does, how we evaluated it, what we found, and how
to talk about it. File references are given where they help you check a claim in the code.

## In one paragraph

Trial turns scientific reasoning into a graph you can check. Every claim points to the exact
passage it came from, every conclusion points to the premises it was drawn from, and a verifier
lists what is still missing. A research agent works on the same graph, but under a guard: before
it may give an answer, an independent model checks its evidence, and the guard only lets it claim
as much certainty as that evidence supports. When the research is verified, the method behind it
can be turned into a step by step lab protocol, and a video of someone doing the experiment can
be checked against it.

## The vocabulary

| Thing | What it is |
|---|---|
| Source | An uploaded paper, a web page or paper the agent read, or a lab video result |
| Excerpt | A passage of a source, at most 1,500 characters, with its exact position |
| Claim (statement) | One assertion, citing the excerpts that contain it. Has a role (premise, conclusion, ...), a salience (core, secondary, supporting) and a review state (proposed, accepted, rejected) |
| Reasoning step | "These premises lead to this conclusion", with a short explanation |
| Link (edge) | supports, rebuts or qualifies between two claims, usually across sources |
| Annotation | A labelled fact about a claim or step: claim strength, causal support, scope change, required premise |
| Obligation | Something that must be established before a claim is justified, raised by a check |
| Workspace | One project. Everything lives in one graph per workspace |

The graph only grows. New papers and agent runs add claims, steps and links; nothing already
there is rewritten. Disagreement is recorded as a new claim with a `rebuts` link. People can
accept or reject nodes, and the agent can withdraw claims that an agent recorded.

## Pipeline 1: adding material

1. **Upload** (Add material mode). PDF, Markdown or text up to 10 MB. Text is split on paragraphs,
   PDFs are parsed page by page with headers, footers and references removed. Each passage becomes
   an excerpt. Identical files are rejected as duplicates.
2. **Extract.** A background worker sends the excerpts to Claude in chunks of about 12,000
   characters. Claude returns claims (each citing at least one excerpt id) and reasoning steps,
   but only links the source itself states: no unstated premises, mechanisms or generalisations.
   Each claim gets a salience: core is the paper's central contribution, secondary is important
   but not central, supporting is the measurements and design facts used as evidence. Everything
   arrives as "proposed". Duplicate claims are dropped, and the call fails if a claim cites an
   excerpt that was not in the chunk.
3. **Connect.** When a second source has claims, the system proposes supports, rebuts and
   qualifies links between core claims from different sources, and a judge model audits each one
   against the cited passages (`supported` or `needs_review`). Existing links are not repeated.
   This also runs from the Verification tab.
4. **Verify.** A rule engine (`services/verification.py`) looks for:
   - claims with no excerpt and no reasoning behind them,
   - steps with missing premises (including those an LLM critic found with "Review reasoning"),
   - causal claims without causal support, and unjustified scope changes,
   - direct conflicts, sources marked invalid or retracted, and limitations the paper admits.

   Each finding opens an obligation you can inspect. "Verified" means the checks have run on the
   current version of the research, not that it passed them.

## Pipeline 2: the research agent

You ask a question, a claim to check, or a hypothesis to test. The agent (Claude Sonnet 5.5)
works in turns, calling tools, until it finishes or runs out of budget. Everything it does is
recorded as a trace you can replay.

**Tools.** It can search the biomedical literature (Amass) and the web, import and read papers,
and query the existing graph (overview, search, read a node, trace a chain). In guarded mode,
which is what the app uses, it also writes to the graph and is checked:

| Tool | What it does |
|---|---|
| `record_claim` | Add a claim, citing excerpts it has read. A causal claim must name the study design that supports it |
| `record_reasoning` | Add a step from premises (any claims in the graph) to a conclusion. Can state a weighing principle, such as "randomised trials outrank observational studies" |
| `link_claims` | Add an audited supports, rebuts or qualifies link to any claim in the graph |
| `revise_claim` | Withdraw an agent claim and point to its replacement |
| `record_protocol` | Record the lab procedure behind the answer as numbered steps, each citing its source |
| `check_conclusion` | Ask the guard what still stands in the way of the conclusion |
| `finalize_conclusion` | Submit the conclusion with a certainty and a one line verdict |
| `abstain` | Stop without a conclusion because the evidence does not support one |

**The guard** (`agent/guardrail.py`). Before any research starts, an independent judge model
(Claude Sonnet 5, not the agent) writes 3 to 5 neutral completion criteria and 1 to 3 falsifiers
for the question. They are fixed before the agent sees any evidence, so they cannot be bent to
fit what it finds. When the agent checks or finalises a conclusion, the guard:

- walks back through the conclusion's reasoning to every claim it rests on,
- runs the verification rules and the critic on that chain,
- asks the judge, which reads the exact passages cited, whether each criterion is met and
  whether each declared causal design is really shown in the text,
- looks for rebuttals of claims in the chain, retracted sources and withdrawn premises.

Each problem becomes an obligation. The certainty the agent may claim is gated by them:

| Certainty | Allowed when |
|---|---|
| established | no critical obligation is open |
| conditional | no hard blocker (retracted source, ungrounded claim, withdrawn premise) |
| hypothesis | always |
| abstain | always |

If the judge cannot be reached, the check fails closed. The agent's own tags ("this claim meets
criterion 2") are only hints; the judge decides from the text. The app shows how settled the
answer is as a named level (exploring, contested, provisional, well supported, settled), never
as a percentage.

**The answer.** In guarded mode the reply is not free text. It is built from what was verified:
the one line verdict, the finalised conclusion claim, and plain limitations for any obligation
still open. Baseline mode (used only in evaluation) is the same model with the same instructions
on answer shape, but without the recording tools, the guard or the judge; its reply is whatever
it writes.

**Follow-ups.** A new question in the same workspace starts a new run that sees the earlier
questions and answers, a compact index of the graph, and the graph tools. It builds on the same
graph rather than starting another.

## Asking the graph

Ask graph mode answers questions about the argument from the graph alone. Claude gets read-only
tools (overview, text search, read a claim with its evidence and links, trace what a claim rests
on or what rests on it) and must finish by naming the claims and steps its answer depends on. The
app highlights that chain and dims the rest. Search is Postgres full-text search; there are no
embeddings. Finished agent runs have a similar "Show reasoning in graph" button.

## From research to the lab

Once the research is verified, the Experiments tab can turn a source's methods section (or a
protocol the agent recorded) into numbered steps with checkable values such as temperatures and
volumes. Every step must trace back to a source passage. A lab video (up to 100 MB) is then
analysed by Claude Opus: it turns each step into visual checks, looks over the whole video,
inspects frames where it needs to, and the code decides each step's verdict (verified,
contradicted or unverifiable, with a 0.7 confidence floor). Observations and deviations are added
back to the graph as reported claims. The bundled DJI_08 sample uses a saved replay of three
observations so the demo is instant; live analysis is available too. Details: `docs/experiments.md`.

## How the evaluation works

The question we wanted answered: does the guard make a research agent more accurate and better
calibrated, or does it just make it slower? So every case is run twice with the same model
(Claude Sonnet 5.5), once in baseline mode and once guarded, each in its own fresh workspace
containing only the supplied source text (web search off). Code: `backend/app/evaluations/`.

### Two benchmarks

- **Full-text papers.** Six open-access biomedical papers from Europe PMC. Claude Opus 5.5 wrote
  30 questions about them, each built around a tempting but wrong inference (for example reading
  a non-significant result as proof of equivalence), with a hidden rubric.
- **SciFact.** 60 claims from the SciFact development set, with the cited abstract. Experts
  labelled each SUPPORTS, CONTRADICTS or NOT ENOUGH INFO; we sampled 20 of each. These labels
  are not ours and no model decides them.

### Scoring

Each answer is scored on its own by Claude Opus 5.5, against the source text, never side by side
with the other arm's answer, so neither length nor order can sway it. For each answer it records:

- every factual claim the answer makes about the source, marked supported, contradicted or
  unsupported (including "the paper does not report X" when it does),
- invalid inferences, such as non-significance read as equivalence or association as causation,
- the verdict the answer commits to,
- whether the 1 to 3 required deductions for the case are drawn with sound reasoning.

Omissions are never errors. For the paper cases, Opus first writes a scoring key per case (the
expected verdict and the required deductions) from the hidden rubric; the expected verdict is
withheld from the scorer. For SciFact the expected verdict is the expert label. Results are given
as paired differences with bootstrap 95% confidence intervals over cases.

### What went wrong first, and why it matters

The first pilot said the guard was much worse (guarded 1.30 against baseline 3.40 out of 4, no
wins in 10). Digging into it showed the evaluation was broken in ways that are themselves the
point of this track:

- **The answer key leaked.** The rubric's "completion criteria" were shown to both agents and
  contained the expected facts and numbers. The baseline read only abstracts yet repeated
  full-text details straight from the leaked key and was rewarded for it. The guarded agent
  refused to state facts it could not cite, and was penalised. The evaluation rewarded the agent
  that was gaming it.
- **Length was mistaken for quality.** A side by side judge with a coverage checklist preferred
  long answers, even when they contained more errors.
- **Real bugs in our pipeline.** Papers were stored as single 80,000 character passages, so the
  judge only ever saw introductions and flagged correct claims as unsupported; finishing on the
  last turn was counted as running out of budget; the guarded answer never stated a verdict; and
  the guard raised false "causal design not shown" warnings on claims that made no causal claim.

All of this is fixed. The pilot is kept as a record of how easy it is to build a benchmark a
science agent can game.

### Results

Guarded minus baseline, with 95% confidence intervals. Lower is better for errors.

**Full-text papers (30 cases)**

| Measure | Baseline | Guarded | Difference |
|---|---:|---:|---|
| Errors per answer (contradicted claims + invalid inferences) | 1.27 | **0.60** | -0.67 (-1.13 to -0.20) |
| Claims the source contradicts | 0.60 | **0.17** | -0.43 (-0.80 to -0.13) |
| Claims the source does not support | 0.73 | **0.40** | -0.33 (-0.67 to -0.03) |
| Invalid inferences | 0.67 | 0.43 | -0.23 (-0.60 to +0.13) |
| Required deductions drawn | 92% | 87% | -4% (-14 to +5) |
| Verdict matches the key | 60% | 53% | -7% (-27 to +13) |
| Factual claims per answer | 16.6 | 9.9 | shorter answers |

**SciFact (60 claims, expert labels)**

| Measure | Baseline | Guarded | Difference |
|---|---:|---:|---|
| Verdict matches the expert label | 90% | 90% | 0 |
| Claims the abstract does not support | 0.35 | **0.07** | -0.28 (-0.48 to -0.10) |
| Errors per answer | 0.18 | 0.12 | -0.07 (-0.25 to +0.13) |

What it means:

- On full papers, the guard halves the errors and cuts false statements about the source by
  about 70%, with no measurable loss in the deductions drawn or the verdicts reached.
- On SciFact, the guard matches the baseline on expert labels and makes about a fifth as many
  unsupported claims.
- Guarded answers are shorter and say less, but more of what they say is true.
- The cost: a guarded answer costs about 3 to 4 times as much (on papers about $0.18 against
  $0.06 per answer), because of the extra turns and judge calls.

Caveats to state honestly: the paper questions come from 6 papers, keys and scores for those are
written by an LLM, "partly supported" against "not supported" verdicts are a judgement call, and
the agent has changed since these runs (graph tools, linking, the protocol step), so they should
be re-run before quoting new figures.

## Pitching it (Track 2: Originator)

**One line.** Trial is a research agent that has to show its working: every claim is tied to the
passage it came from, an independent checker decides what the evidence supports, and the agent
is only allowed as much certainty as it has earned.

**How it fits the three strands.**

1. *Benchmarking science agents and reward hacking.* Our own first benchmark rewarded the agent
   that gamed it, by leaking the answer key and favouring length. We built the fix: blind arms
   that see only the question, single-answer scoring that ignores length, claim by claim checks
   against the source, and expert-labelled SciFact cases. Inside the agent, the criteria are set
   before research, and the agent cannot mark its own homework: its tags are hints, a separate
   model reads the cited text.
2. *Lab automation and safety with testable results.* Only verified research becomes a protocol,
   every protocol step traces to a source passage, and a lab video is checked step by step against
   it, with "unverifiable" as an honest outcome rather than a guess.
3. *Epistemological agents.* Certainty is earned, not declared: established, conditional,
   hypothesis or abstain, gated by open obligations. Falsifiers are written up front. The app shows
   what is holding a conclusion back in words, and the agent abstains when the evidence does not
   support any answer.

**A 60 second version.** Research agents sound confident whether or not they are right, and the
benchmarks we use to grade them can be gamed. Trial makes the reasoning visible: claims link to
the exact passages they came from and conclusions link to their premises, so you can see the
argument. The agent works under a guard. Before it researches, an independent model sets what
would count as an answer. Before it answers, that model reads the cited passages and the guard
caps its certainty at what the evidence supports. In a blind evaluation on 30 full-text paper
questions, the same model under the guard made half as many errors and about 70% fewer false
statements about its sources, while matching the baseline's verdicts on 60 expert-labelled
SciFact claims. Our first evaluation said the opposite, because it leaked the answer key to the
agents, and finding that is part of what we built. Verified research then becomes a lab protocol
that a video of the experiment is checked against.

**Demo flow.** Load the DJI_08 sample, run verification and open an obligation; ask the agent a
question and watch the trace, the uncertainty bar and the certainty it is allowed; use Ask graph
mode ("what does this conclusion rest on?") to show the highlighted chain; continue to
Experiments and show the protocol checks against the video.

**Say this, not that.**

- Say "fewer errors and fewer unsupported claims, same verdict accuracy". Do not say "more
  accurate overall" or "verifies truth".
- Say "verification means the checks ran and the gaps are listed". Do not say "verified claims are
  true".
- Say "independent judge model". It is still an LLM, from the same family; do not call it a human
  or a ground truth.
- Say "LLM-scored, plus expert-labelled SciFact". Do not present the paper benchmark as
  independent validation.

**Likely questions.**

- *Why should we trust a judge that is also an LLM?* It is a separate call that only sees the
  cited text, the criteria are fixed before research, and it fails closed. On SciFact the ground
  truth is human.
- *Is it just more cautious?* Its verdicts match the baseline on SciFact (90% each), so it is not
  winning by refusing to answer.
- *What does it cost?* About 3 to 4 times a plain agent per answer, in exchange for half the
  errors on the paper benchmark.
- *What is next?* Re-running the benchmarks on the current agent, linking lab results back to the
  claims they test, and a larger set of papers.
