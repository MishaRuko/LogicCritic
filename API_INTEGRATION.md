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

A live research agent answers a question by searching the literature (Amass) and the web, reading what it relies on, and recording claims and reasoning in the same graph as uploaded sources. Runs cost real model tokens, so one only starts on an explicit `POST`, and every run has a budget. Requires `CLAUDE_API_KEY` (503 otherwise); `AMASS_API_KEY` enables paper search, and web search needs no key.

- `POST /workspaces/{workspaceId}/agent-runs` with `{ "idempotency_key": "...", "question": "...", "kind"?: "question" | "claim" | "hypothesis", "completion_criteria": ["..."], "falsifiers": ["..."], "mode": "guarded" | "baseline", "model"?, "max_turns"?, "max_web_searches"? }` queues a run and returns it (202). `guarded` (default) gives the agent the verifier; `baseline` removes it, for comparison. Defaults come from `AGENT_MODEL` (Sonnet 5.5), `AGENT_MAX_TURNS` and `AGENT_MAX_WEB_SEARCHES`. `question` is whatever is being assessed: an open question, a claim to check or a hypothesis to test (`kind` tailors the instructions; a hypothesis is tested by looking for what would falsify it). `completion_criteria` and `falsifiers` are optional: when a guarded run is given none, it proposes its own before any research starts (event `criteria_proposed`, or `criteria_default` with generic criteria if that fails), so the answer is always held to a stated standard.
- `GET /agent-runs/{runId}` returns `status` (`queued`, `running`, `succeeded`, `failed`, `cancelled`, `budget_exhausted`), `budgets`, `usage` (tokens, turns, web searches and an estimated `cost_usd`), `error`, `final_report`, `final_statement_id` and `certainty` (`established`, `conditional`, `hypothesis`, `abstained`).
- `GET /agent-runs/{runId}/events?after={seq}&limit=500` is the run's trace, in order. Poll with `after` set to the last `seq` you have. Event types: `run_started`, `turn` (one per model call: stop reason, latency and that call's token usage), `thinking` (summarised reasoning), `assistant_text`, `web_search`, `web_results`, `tool_call`, `tool_result`, `check`, `finalization`, `graph_change` (each change the agent makes to the graph as it happens: `claim_added`, `step_added`, `claim_superseded`, `claim_withdrawn`; `position: true` marks a change to the agent's working conclusion, so the first one is its initial position and the rest are revisions with a `reason`), `nudge`, `criteria_given` / `criteria_proposed` / `criteria_default`, `server_block` (any other server-side tool output, recorded as it arrived) and `run_finished`.
- `GET /workspaces/{workspaceId}/agent-runs` lists runs, newest first. `DELETE /agent-runs/{runId}` cancels one that is queued or running.

**How the graph is built.** The agent records claims (`record_claim`, with the excerpt ids that support them) and reasoning steps (`record_reasoning`) through tools. They appear as ordinary `proposed` statements and reasoning steps whose provenance has `actor_type: "agent"` and the `run_id`, so the graph views already render them. Web pages it fetches become sources of kind `web_page`, with excerpts, like any other source. The agent forms a working position after its first reading, builds the graph as it reads (each `read_source` result reminds it to record what it will rely on), and amends it: `revise_claim` withdraws a claim it recorded (it is marked `rejected` and, when replaced, linked to its replacement by a `revises` relation and `superseded_by`). A reasoning step that still relies on a withdrawn claim is stale, and the guardrail refuses any certainty above `hypothesis` until it is re-recorded.

**The guardrail** (guarded runs). Before answering, the agent calls `check_conclusion`, which returns the open obligations on the claim and everything it rests on: an invalidated (for example retracted) source, an ungrounded claim, a reasoning step needing an unstated premise (the critic's own reason is quoted, so the agent knows what to supply). A step whose conclusion depends on weighing evidence can declare the principle (`weighing` on `record_reasoning`, for example randomised over observational): the critic accepts a recognised, fitting principle instead of calling it an unstated premise, and still flags a missing or ad hoc one, an asserted conclusion with no recorded reasoning behind it (`unreasoned_conclusion`; citing excerpts does not replace the reasoning), opposing evidence the agent has not accounted for, or a completion criterion that is not met. Criteria, and the study design behind each causal claim (`causal_support`), are decided by an independent reviewer model (`AGENT_JUDGE_MODEL`) that reads the exact text each claim cites and the searches the agent ran; the agent's own `criteria_satisfied` tags are only hints. A criterion that is only partly met counts as not met, and each verdict, with the evidence the reviewer was shown, is stored per run (`judge_verdicts`) so a check can be audited and is not repeated for unchanged evidence. If the reviewer cannot run, the check fails closed (`judge_unavailable`: no `established`). All reviewer calls (this judge, the guardrail's premise critic and cross-source linking) share one token tally, included in `usage.cost_usd` (`usage.judge`). It says what must be established, not what to do. `finalize_conclusion` is enforced on the server: `established` is refused while critical obligations are open, `conditional` is refused if the claim rests on an invalidated or ungrounded source, and the agent must then narrow its claim, accept a weaker certainty, or abstain. Obligations raised for opposing evidence and unmet criteria also appear as proof obligations on the claim.

## UI Rules

- Keep workspace data isolated; never combine graph IDs between workspaces.
- Render provenance, excerpt links, lifecycle, and audit status beside every generated node or edge.
- Use idempotency keys for every mutating operation; safely retrying an identical request returns the original result.
- Persist no API key in the frontend. Claude calls are backend-only.
