# Integration notes


This integration uses only implemented backend endpoints. CSV statistical challenges, OpenRouter settings, Amass retrieval, agent replay, semantic search and goal management from the original Trial app have been replaced with supported document/argument workflows.

The API has no workspace source/job listing, excerpt lookup by ID, annotation listing, source-validity read endpoint, or event-history endpoint. Therefore:

- Returned source/job IDs and source-validity decisions are remembered in this browser, scoped to each workspace. Exact text and job state are always fetched from the backend. Use **Reconnect a source or extraction job** in Material with IDs from another session or the exported snapshot. A missing source appears as an unavailable linked excerpt, never an invented quote.
- Source validity shown in the inspector is explicitly labelled as the last decision in this browser; the verifier reads authoritative backend validity.
- Statement context exposes statement issues and obligations. Reasoning-step findings are included in verification totals but cannot be fetched in detail through the current API. The UI states this limitation rather than claiming complete visibility.
- Annotation submissions show the returned patch ID; full server annotation history is unavailable. Verification and Claude action summaries are the actual responses from the current browser session, not a durable event ledger.

Claude actions require `CLAUDE_API_KEY` and a running worker for extraction. Missing credentials or provider failures appear as real API/job errors. Manual graph editing, review, source validity and deterministic verification remain usable without a model provider.
