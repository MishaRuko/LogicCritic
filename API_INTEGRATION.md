# API Integration

All API routes are served under `/api`. The backend is the source of truth; all model-generated statements, reasoning, annotations, and cross-source links are `proposed` until a user reviews them.

## Workspace Flow

1. `POST /workspaces` with `{ "title": "..." }` creates a session.
2. `POST /workspaces/{workspaceId}/sources` as multipart form data with `file` uploads a UTF-8 `.txt`, `.md` or `.markdown` file, or a text-based `.pdf`, and returns immutable excerpts. PDF excerpts carry `page`, `start`, `end` and (when a heading is recognised) `section` in `locator`; the reference list is skipped. Scanned PDFs, password-protected PDFs and PDFs over 300 pages are rejected with a reason (422 or 413).
3. `POST /sources/{sourceId}/extract` with `{ "idempotency_key": "..." }` queues Claude extraction.
4. Poll `GET /extraction-jobs/{jobId}` until `status` is `succeeded`, `failed`, or `cancelled`.
5. `GET /workspaces/{workspaceId}/graph` returns statements, reasoning steps, and relations for graph rendering.

Statements include `excerpt_ids` and `salience` (`core`, `secondary`, or `supporting`); retrieve excerpt text with `GET /sources/{sourceId}/excerpts`. `core` is the minimum set expressing the paper's central contribution, `secondary` covers consequential but noncentral findings and implications, and `supporting` is direct evidence or design used as a premise. Reasoning steps contain `premise_ids` and `conclusion_id`. Extraction has no numeric claim cap: it retains every consequential claim but excludes standalone background and routine procedure. AMASS titles remain document context rather than graph claims. Retraction-notice excerpts remain available as source evidence but are excluded from scientific claim extraction because source validity represents the retraction itself.

For chunked sources, later chunks receive the claims already extracted from that same source and must not repeat or paraphrase them. The backend also rejects exact normalized duplicates within a source. A `supporting` statement must name the core or secondary claim it is evidence or a premise for (`supports_ref`); one that supports nothing kept is background and is dropped, along with steps that relied on it. Claims from different sources are never deduplicated this way because their independent provenance matters for synthesis.

When an AMASS record includes full text, its abstract is used for the document overview but is not independently extracted into graph claims; the full text remains the claim-bearing source. Abstract-only records continue to extract from the abstract.

## Review And Verification

- `POST /workspaces/{workspaceId}/verify` runs deterministic checks. Render resulting issues and proof obligations from `GET /workspaces/{workspaceId}/statements/{statementId}/context`.
- `POST /workspaces/{workspaceId}/argument-check` with `{ "idempotency_key": "..." }` runs the conservative evidence-only critic over all reasoning steps. It writes an existing `required_premise` annotation only when a step needs an unstated bridge; a subsequent `/verify` opens `missing_premise` issues.
- `POST /workspaces/{workspaceId}/review` accepts a user-only decision. Do not expose model output as accepted state before this action.

## Cross-Source Synthesis

`POST /workspaces/{workspaceId}/synthesize` with `{ "idempotency_key": "..." }` considers statements from different uploaded sources. It creates only proposed statement-to-statement relations:

Synthesis considers all `core` statements in the workspace; it does not truncate the candidate list. Keep `secondary` and `supporting` statements available in source/evidence views rather than presenting them as paper-level synthesis candidates.

- `supports`: independently compatible evidence for substantially the same proposition.
- `rebuts`: directly incompatible propositions.
- `qualifies`: an explicit narrower scope, condition, or limitation.

Each relation has metadata shaped as:

```json
{
  "lifecycle": "proposed",
  "generator_rationale": "Why the first pass proposed the link",
  "audit_verdict": "supported | needs_review",
  "audit_rationale": "Evidence-only second-pass assessment",
  "model": "claude-sonnet-5"
}
```

Show `needs_review` links as warnings, not corroboration or contradiction. The backend deliberately does not create claim clusters or normalized claim labels; connected proposed relations are the visual cluster and remain inspectable at the statement/excerpt level.

## Amass Literature

Requires `AMASS_API_KEY` on the backend; without it these endpoints return 503. Amass is called only by the backend, at most 60 requests a minute, and each record is cached by its Amass ID so a repeat import spends no API credits.

- `POST /integrations/amass/search` with `{ "query": "...", "limit": 10 }` searches BiomedCore and saves nothing. Optional filters: `min_publication_date`, `max_publication_date` (`YYYY-MM-DD`), `min_citation_count`, `is_retracted`. Each result has `amass_id`, `pmid`, `pmcid`, `doi`, `title`, `abstract_preview`, `authors`, `journal`, `publication_date`, `citation_count`, `is_retracted` and `has_fulltext`.
- `POST /workspaces/{workspaceId}/integrations/amass/import` with exactly one of `amass_id`, `pmid` or `doi` (and optionally `"include_fulltext": false`) creates a source and returns `{ source, already_imported, retracted }`: 201 when new, 200 when the workspace already has that paper. A PMID or DOI is resolved to an Amass ID first; an unknown one is a 404.
- The source has `kind: "amass_record"`, `origin: "amass"` and `external_ids` holding `amass_id`, `pmid`, `pmcid` and `doi`. Its excerpts are the title, the abstract and any full text, located by `locator.jsonPath` (`title`, `abstract` or `fulltext`) with `start` and `end` offsets into that field, and `section` where the full text has headings. The raw Amass record is stored as a JSON snapshot with its retrieval time. These excerpts extract, verify and synthesize exactly like uploaded ones.
- If Amass reports the paper as retracted (it mirrors PubMed retraction notices), the source is recorded as `invalidated` with provenance `integration / amass`, and `/verify` then opens `invalidated_source` issues on any statement grounded in it. `POST /sources/{sourceId}/amass/refresh` re-reads the record and does the same for a paper retracted after import; it returns `{ retracted, newly_invalidated }`.
- `GET /sources/{sourceId}/validity` returns the latest validity decision, whoever made it, or 404 if none. Show a retraction from here rather than from browser-remembered decisions.

Amass failures are reported, never hidden: 404 for an unknown record, 429 with `Retry-After` when rate limited, 422 for a record with nothing to import, and 502 for a rejected key or an Amass outage. A failed import creates nothing.

## Research Agent

A live research agent assesses an open question, a claim or a hypothesis. It searches the literature (Amass) and the web, reads what it relies on, and records claims and reasoning in the same graph as uploaded sources, so every graph view already renders its work. Types for everything below are in `frontend/src/types/api.ts` (`AgentRun`, `AgentEvent`, `Assurance`, `AgentVerdict`).

Runs cost real model tokens, so one only starts on an explicit `POST`, and each has a budget. Requires `CLAUDE_API_KEY` (503 otherwise). `AMASS_API_KEY` enables paper search. Web search needs no key.

- All run responses include the goal's `question` and `kind` for durable history labels. `GET /workspaces/{workspaceId}/sources` lists all workspace sources, including papers and web pages imported by the agent; fetch their excerpts through the existing source endpoints.
- Chat messages use the same start endpoint, including while another run is active. Workers process queued messages in order within a workspace. Each new run receives earlier completed questions and answers plus the current workspace’s source and graph index, so follow-ups can reuse evidence and extend the graph. The worker ignores a model response that arrives after its run was cancelled.

### Endpoints

| Call | Returns |
|---|---|
| `POST /workspaces/{workspaceId}/agent-runs` | 202 and the queued run. Body: `idempotency_key`, `question`, optional `kind` (`question`, `claim` or `hypothesis`), `completion_criteria`, `falsifiers`, `mode` (`guarded` default, or `baseline`), `model`, `max_turns`, `max_web_searches`. |
| `GET /agent-runs/{runId}` | The run, with its `goal`, `status`, `budgets`, `usage`, `certainty`, `final_report` and `final_statement_id`. Poll it. |
| `GET /agent-runs/{runId}/events?after={seq}&limit=500` | The trace in order. Pass the last `seq` you have as `after`. |
| `GET /agent-runs/{runId}/verdicts` | The independent reviewer's judgements of the evidence against the completion criteria, oldest first. The last is current. |
| `GET /workspaces/{workspaceId}/agent-runs` | Runs, newest first, each with its goal. |
| `DELETE /agent-runs/{runId}` | Cancels a queued or running run. |
| `GET /workspaces/{workspaceId}/sources` | Every source in the workspace, including the papers and web pages an agent fetched. Use it to resolve the sources behind a claim; do not rely on IDs remembered in the browser. |

`question` is whatever is being assessed. `kind` tailors the agent's instructions: a claim is checked starting from refutation, and a hypothesis is tested by looking for what would falsify it. `guarded` runs have the verifier; `baseline` removes it, for comparison. Defaults come from `AGENT_MODEL`, `AGENT_MAX_TURNS` and `AGENT_MAX_WEB_SEARCHES`.

`status` is `queued`, `running`, `succeeded`, `failed`, `cancelled` or `budget_exhausted`. `certainty` is `established`, `conditional`, `hypothesis` or `abstained` once the agent has concluded. `usage` holds tokens, turns, web searches, `cost_usd` (the agent plus all reviewer calls) and `assurance`.

### Completion criteria

Criteria are the standard of evidence the answer is held to. If you supply none, a guarded run proposes its own before any research starts, so the standard cannot be fitted to what it later finds. They are written to be neutral about the answer: they say what evidence would settle it either way. The trace shows which happened (`criteria_given`, `criteria_proposed`, or `criteria_default` with generic criteria if proposing failed), and `goal.completion_criteria` always holds the criteria in force.

Whether each criterion is met is decided by an independent reviewer model (`AGENT_JUDGE_MODEL`) that reads the exact text every claim cites and the agent's search queries. The agent's own `criteria_satisfied` tags are only hints. A criterion that is only partly met counts as not met. `GET .../verdicts` returns each review: per criterion `met`, a `rationale` (including what is still missing), and the claims that support it, plus whether each causal claim's cited text actually shows the study design the agent declared. If the reviewer cannot run, the check fails closed.

### The uncertainty bar (`assurance`)

How settled the answer is, as a named level and never a number. Percentages would claim a precision nothing here has. The level is read off the verifier's state, so it moves only when the argument does: it rises as the agent closes what the verifier found, drops when the agent or the verifier finds a flaw, and reaches `settled` only when an `established` answer is accepted.

```json
{ "level": "provisional", "label": "Evidence gaps remain",
  "scale": ["unexplored", "exploring", "contested", "provisional", "well_supported", "settled"],
  "holding_back": [{ "kind": "unmet_criteria", "description": "Completion criterion 1 is not met: ..." }] }
```

| Level | Meaning |
|---|---|
| `unexplored` | No position recorded yet. |
| `exploring` | The agent holds a position it has not had verified. |
| `contested` | The verifier found a flaw in the argument itself (a retracted source, an unsupported claim, unaddressed opposing evidence, a causal overclaim, a scope leap, reasoning resting on a withdrawn claim, a conclusion with no reasoning). |
| `provisional` | The argument is sound as far as it goes, but evidence gaps remain (an unmet criterion, a missing premise). |
| `well_supported` | Nothing critical found against it; not yet finalized as established. |
| `settled` | An `established` answer was accepted. |

`scale` is ordered from most to least uncertain, so draw one segment per entry and fill up to `level`. `holding_back` says in words what stands in the way (at most five); show it beside the bar. Do not interpolate between segments or label them with numbers. The level can go down as well as up, for example when a better search turns up opposing evidence. `finalize` with `conditional` or `hypothesis` leaves the level where the last check put it, which is the honest picture of a caveated answer.

Where to read it: the latest `assurance` event, or `usage.assurance` on the run. It is `null` for `baseline` runs, which have no verifier. Each `check` event also carries it.

### The trace

Every event has `seq`, `type`, `payload` and `created_at`. Payload types are in `AgentEvent`.

| Type | Payload | Use |
|---|---|---|
| `run_started` | `mode`, `model`, `budgets` | Header. |
| `criteria_given` / `criteria_proposed` / `criteria_default` | `criteria`, `falsifiers` | Show the standard of evidence. |
| `turn` | `turn`, `stop_reason`, `model`, `latency_s`, `usage` | One per model call. |
| `thinking` | `text` (summarised), or `redacted: true` | Reasoning shown to the user. |
| `assistant_text` | `text` | What the agent said. |
| `web_search`, `web_results` | `query`; `results[{url,title}]` or `error` | Searches. |
| `tool_call`, `tool_result` | `name`, `input`; `result`, `is_error` | Raw actions. Prefer the specialised events below for display. |
| `graph_change` | see below | Animate the graph as it is built. |
| `assurance` | the assurance object | Move the uncertainty bar. |
| `check` | `result`: the verifier's packet | Obligations and what the conclusion rests on. |
| `protocol` | `source_id`, `title`, `basis`, `steps[{n, action, excerpt_ids}]` | The procedure the agent handed over. |
| `experiment_ready` / `experiment_not_ready` | `protocol_id`, `source_id`, `steps`; or `source_id`, `reason` | The protocol is prepared for the experiment tools, or why it is not. |
| `finalization` | `result`: `accepted`, `certainty`, `caveats` or `reason` and `obligations` | The attempt to conclude; a refusal is shown too. |
| `nudge` | `reason`, `count` | The agent was prompted to continue. |
| `server_block` | `block` | Any other server-side tool output, as recorded. |
| `run_finished` | `status`, `error`, `certainty`, `usage` | Always the last event. |

**`graph_change`** tells you what the agent did to the graph, as it happens: `claim_added`, `step_added`, `claim_superseded` (replaced by a corrected claim) and `claim_withdrawn`. `position: true` marks a change to the agent's working conclusion. The first is its initial position and the rest are revisions, each with a `reason`, so a replay can show the belief changing. Fetch the full graph with `GET /workspaces/{id}/graph`; use these events to know what changed and when.

**The `check` packet** has `conclusion`, `evidence_chain` (each claim with its sources and whether a source is `retracted`), `reasoning_steps`, `obligations`, `completion_criteria`, `can_finalize_as`, `assurance` and `notes`. Each obligation has `kind`, `severity` (`critical` or `advisory`), `description`, `required_condition` and `applies_to` (a statement or step id).

### How the graph is built and amended

The agent forms a working position after its first reading, then records claims as it reads (`record_claim`, with the excerpt ids that support them) and the reasoning that combines them (`record_reasoning`). They appear as ordinary `proposed` statements and reasoning steps whose provenance has `actor_type: "agent"` and the `run_id`. Web pages it fetches become sources of kind `web_page`.

It also amends the graph. `revise_claim` withdraws a claim: the statement becomes `rejected`, its `superseded_by` is set when a replacement exists, and a `revises` relation (with the reason in `metadata.reason`) runs from the replacement to the old claim. Render rejected agent claims as withdrawn, not deleted, and draw the `revises` edge so the history is visible. A reasoning step can be replaced the same way. A step may carry a `weighing` annotation (`custom:weighing`, `value.principle`): the principle used to weigh evidence, such as randomised over observational.

### The guardrail

Before answering, the agent calls `check_conclusion`, which returns the open obligations on its conclusion and everything the conclusion rests on. It says what must be established, not what to do. `finalize_conclusion` is enforced on the server: `established` is refused while a critical obligation is open, `conditional` is refused if the claim rests on a hard blocker, and the agent must then narrow its claim, accept a weaker certainty, or abstain.

| Kind | Severity | Meaning |
|---|---|---|
| `invalidated_source` | critical, hard blocker | A claim rests on a retracted or invalidated source. |
| `ungrounded_statement` | critical, hard blocker | A claim cites no source text. |
| `withdrawn_premise` | critical, hard blocker | A reasoning step still relies on a claim the agent withdrew. |
| `unresolved_conflict` | critical | Opposing evidence the agent has not accounted for. |
| `unreasoned_conclusion` | critical | An asserted conclusion with no recorded reasoning behind it. |
| `missing_premise` | critical | The critic found a premise the step needs but does not state (its reason is quoted). A declared `weighing` that is a recognised, fitting principle counts as stated. |
| `causality_overclaim`, `scope_leap` | critical | A causal claim without a causal design, or evidence carried to a different population or model. |
| `causal_design_not_shown` | critical | The cited text does not show the study design the agent declared. |
| `unmet_criteria` | critical | A completion criterion is not met. |
| `judge_unavailable` | critical | The reviewer could not run, so the check fails closed. |
| `possible_conflict` | advisory | A possible conflict whose link the audit doubts. |

Agent-raised obligations also appear as proof obligations on the claim, so the existing obligation views show them.

### The protocol hand-off

When the evidence the agent relied on describes a procedure someone could carry out to reproduce or test the finding, the agent hands it over, whether or not the question asked for one. It calls `record_protocol` with the physical bench steps in order, each citing the excerpts that state it, plus a `basis` saying which source(s) it follows and why where sources differ. It reports only what the sources state: if the details are not in the text it read, the protocol is short and the basis says so. It never invents a step.

- **Where it appears.** `AgentRun.protocol` (`source_id`, `title`, `basis`, `steps[{n, action, excerpt_ids}]`, or `null` if none), a `protocol` event in the trace, and a source in the workspace (`origin: "agent"`, title `Protocol: ...`, a `# Methods` section of numbered steps, step citations in `metadata.steps`). Each step's `excerpt_ids` point at the paper passages it came from, so show provenance from there.
- **How it is enforced.** `finalize_conclusion` makes the agent state `protocol`: `recorded` or `none`. Claiming `recorded` without having recorded one is refused. Saying `none` after reading a source with a Methods section is pushed back once (never a loop); the agent can then record a protocol or finalize again.
- **Retractions.** A protocol cannot cite a retracted source or experiment results.
- **Into the experiment tools, automatically.** When a run concludes with a protocol, the backend runs the deterministic verifier once more (recording the protocol changed the research) and prepares the protocol for the experiment tools. It is an ordinary source with a numbered Methods section, so it extracts verbatim with no model call. The trace gets `experiment_ready` (`protocol_id`, `source_id`, `steps`) or, if it could not be prepared, `experiment_not_ready` with a `reason`; the run itself never fails because of this. `AgentRun.protocol.experiment_protocol_id` is the prepared protocol. Only the recording is left for a person to supply.
- **Starting the experiment.** `POST /workspaces/{id}/experiment-runs` with `mode` and the recording, and **no** `protocol_id` or `source_id`, uses the agent's prepared protocol as long as it is still current (the research has not changed since). If it is stale, or there is none, the call returns 422 asking for a source. Send `protocol_id` (or `source_id`) to choose explicitly; a client that always sends the selected `source_id` will use that source instead, so make the agent's protocol the default selection.
- **Preparing one by hand.** `POST /workspaces/{id}/protocols {source_id}` still works for any source. Run Checks first. Volume and temperature checks are created for steps that give a single value and, for volumes, name a pipette; ranges ("1–5 µl") and negatives are left as instructions.

### Rendering a run

A layout that works well: the uncertainty bar and `holding_back` at the top; a timeline of the trace on one side (thinking, searches, `check`, `finalization`) and the live graph on the other, updated from `graph_change` events; the completion criteria with their latest verdicts; and the final report with `certainty` and the caveats from the `finalization` event. Poll events every second or two while `status` is `queued` or `running`, then once more after it ends. Always show cost (`usage.cost_usd`) and which model ran.

## UI Rules

- Keep workspace data isolated; never combine graph IDs between workspaces.
- Render provenance, excerpt links, lifecycle, and audit status beside every generated node or edge.
- Use idempotency keys for every mutating operation; safely retrying an identical request returns the original result.
- Persist no API key in the frontend. Claude calls are backend-only.
