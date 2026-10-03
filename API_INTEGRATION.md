# API Integration

All API routes are served under `/api`. The backend is the source of truth; all model-generated statements, reasoning, annotations, and cross-source links are `proposed` until a user reviews them.

## Workspace Flow

1. `POST /workspaces` with `{ "title": "..." }` creates a session.
2. `POST /workspaces/{workspaceId}/sources` as multipart form data with `file` uploads a UTF-8 `.txt`, `.md` or `.markdown` file, or a text-based `.pdf`, and returns immutable excerpts. PDF excerpts carry `page`, `start`, `end` and (when a heading is recognised) `section` in `locator`; the reference list is skipped. Scanned PDFs, password-protected PDFs and PDFs over 300 pages are rejected with a reason (422 or 413).
3. `POST /sources/{sourceId}/extract` with `{ "idempotency_key": "..." }` queues Claude extraction.
4. Poll `GET /extraction-jobs/{jobId}` until `status` is `succeeded`, `failed`, or `cancelled`.
5. `GET /workspaces/{workspaceId}/graph` returns statements, reasoning steps, and relations for graph rendering.

Statements include `excerpt_ids` and `salience` (`core` or `supporting`); retrieve excerpt text with `GET /sources/{sourceId}/excerpts`. Reasoning steps contain `premise_ids` and `conclusion_id`. Extraction has no numeric claim cap: it retains every consequential claim, while marking details used only as evidence as `supporting`. AMASS titles remain document context rather than graph claims. Retraction-notice excerpts remain available as source evidence but are excluded from scientific claim extraction because source validity represents the retraction itself.

## Review And Verification

- `POST /workspaces/{workspaceId}/verify` runs deterministic checks. Render resulting issues and proof obligations from `GET /workspaces/{workspaceId}/statements/{statementId}/context`.
- `POST /workspaces/{workspaceId}/argument-check` with `{ "idempotency_key": "..." }` runs the conservative evidence-only critic over all reasoning steps. It writes an existing `required_premise` annotation only when a step needs an unstated bridge; a subsequent `/verify` opens `missing_premise` issues.
- `POST /workspaces/{workspaceId}/review` accepts a user-only decision. Do not expose model output as accepted state before this action.

## Cross-Source Synthesis

`POST /workspaces/{workspaceId}/synthesize` with `{ "idempotency_key": "..." }` considers statements from different uploaded sources. It creates only proposed statement-to-statement relations:

Synthesis considers all `core` statements in the workspace; it does not truncate the candidate list. Keep `supporting` statements available in source/evidence views rather than presenting them as paper-level synthesis candidates.

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

## UI Rules

- Keep workspace data isolated; never combine graph IDs between workspaces.
- Render provenance, excerpt links, lifecycle, and audit status beside every generated node or edge.
- Use idempotency keys for every mutating operation; safely retrying an identical request returns the original result.
- Persist no API key in the frontend. Claude calls are backend-only.
