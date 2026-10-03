# API Integration

All API routes are served under `/api`. The backend is the source of truth; all model-generated statements, reasoning, annotations, and cross-source links are `proposed` until a user reviews them.

## Workspace Flow

1. `POST /workspaces` with `{ "title": "..." }` creates a session.
2. `POST /workspaces/{workspaceId}/sources` as multipart form data with `file` uploads UTF-8 `.txt`, `.md`, or `.markdown` content and returns immutable excerpts.
3. `POST /sources/{sourceId}/extract` with `{ "idempotency_key": "..." }` queues Claude extraction.
4. Poll `GET /extraction-jobs/{jobId}` until `status` is `succeeded`, `failed`, or `cancelled`.
5. `GET /workspaces/{workspaceId}/graph` returns statements, reasoning steps, and relations for graph rendering.

Statements include `excerpt_ids`; retrieve excerpt text with `GET /sources/{sourceId}/excerpts`. Reasoning steps contain `premise_ids` and `conclusion_id`.

## Review And Verification

- `POST /workspaces/{workspaceId}/verify` runs deterministic checks. Render resulting issues and proof obligations from `GET /workspaces/{workspaceId}/statements/{statementId}/context`.
- `POST /workspaces/{workspaceId}/argument-check` with `{ "idempotency_key": "..." }` runs the conservative evidence-only critic over all reasoning steps. It writes an existing `required_premise` annotation only when a step needs an unstated bridge; a subsequent `/verify` opens `missing_premise` issues.
- `POST /workspaces/{workspaceId}/review` accepts a user-only decision. Do not expose model output as accepted state before this action.

## Cross-Source Synthesis

`POST /workspaces/{workspaceId}/synthesize` with `{ "idempotency_key": "..." }` considers statements from different uploaded sources. It creates only proposed statement-to-statement relations:

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
  "model": "claude-sonnet-4-5-20250929"
}
```

Show `needs_review` links as warnings, not corroboration or contradiction. The backend deliberately does not create claim clusters or normalized claim labels; connected proposed relations are the visual cluster and remain inspectable at the statement/excerpt level.

## UI Rules

- Keep workspace data isolated; never combine graph IDs between workspaces.
- Render provenance, excerpt links, lifecycle, and audit status beside every generated node or edge.
- Use idempotency keys for every mutating operation; safely retrying an identical request returns the original result.
- Persist no API key in the frontend. Claude calls are backend-only.
