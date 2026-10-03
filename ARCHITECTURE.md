# Research Argument Graph: Architecture and Implementation Plan

## 1. Purpose

Build a persistent epistemic layer for scientific research materials and research agents.

The system receives papers, notes, source records, and structured agent activity. It builds an auditable graph of statements, evidence, assumptions, reasoning steps, contradictions, and unresolved proof obligations. It does not decide scientific truth. It makes clear:

- what a conclusion depends on;
- what source text or data supports each statement;
- where an inference crosses an unsupported boundary;
- what new evidence weakens or changes downstream conclusions;
- what must be established before an agent can responsibly finalize a conclusion.

The same backend supports two product modes:

1. **Material review:** a user uploads research material and explores an incrementally constructed argument graph.
2. **Agent guardrail/evaluation:** a research agent records structured milestones while it works. Before it states a material conclusion, the system checks its graph state and returns unresolved proof obligations. The agent, not the system, chooses how to resolve them.

This is not a general scientific workbench, agent orchestrator, or replacement for Claude Science. It is an explicit, model-agnostic argument and epistemic-memory layer that may be used by any agent or workbench.

## 2. Product Positioning

### The problem

Research agents can produce compelling reports that satisfy superficial objectives while failing scientifically. Examples:

- treating correlation as causal evidence;
- treating cell-line or mouse evidence as human clinical evidence;
- mistaking the existence of a clinical trial for evidence of efficacy;
- using a retracted source;
- ignoring a directly conflicting result;
- reporting a confident answer despite an unmet critical premise.

Citation lists and reproducible code are useful but insufficient. A citation may not entail the claim made from it. A perfectly reproducible analysis may still rest on an invalid extrapolation.

### The system's role

The system is a verifier and state layer. It does not replace the research agent.

| Component | Responsibility |
| --- | --- |
| Evidence ledger | Stores sources, excerpts, tool outputs, identifiers, and provenance. |
| Argument graph | Stores natural-language statements and explicit reasoning steps. |
| Verifier | Runs deterministic checks where structured information is available. |
| Planner agent | Chooses which proof obligation to resolve and proposes an action. |
| Execution agent | Searches sources, queries tools such as Amass, runs analysis, and proposes graph updates. |

The core distinction is:

> The verifier says what is required to justify a claim. The agent decides what to do next.

The system may create a proof obligation such as "direct human outcome evidence is absent." It must not claim that the next action is necessarily "run experiment X." A planning agent may choose to retrieve a trial result, narrow the conclusion, propose an experiment, or abandon the claim.

## 3. Principles and Non-Goals

### Principles

1. **Natural language first.** Original statements and source excerpts are never replaced by a lossy formal representation.
2. **Progressive formalisation.** Structure is added where it is useful and supported, not required for every claim.
3. **Provenance by default.** Every extracted or agent-proposed object records its source, producer, model/version, and timestamp.
4. **Append-only history.** Corrections and new evidence create revisions/events rather than silently overwriting prior state.
5. **Deterministic checks after extraction.** LLMs may propose structure. Rule evaluation over accepted structure must be deterministic and explainable.
6. **Human inspectability.** Every issue must link to the exact statement, reasoning step, and original source material involved.
7. **Model agnostic integration.** The API/MCP contract works with OpenRouter models, Claude, custom agents, and future systems.
8. **No automatic semantic merge.** Similar statements may be linked, but are never silently merged because an embedding says they look similar.

### Non-goals for the hackathon

- Proving scientific truth or replacing expert review.
- A complete first-order logic or theorem prover.
- A complete ontology for all scientific domains.
- A full research-agent platform, laboratory platform, or notebook environment.
- Perfect PDF extraction or complete coverage of every argument in a long paper.
- Automatically choosing experiments without an agent or human decision maker.
- Large-scale graph-database infrastructure.

## 4. The Conceptual Model

### Why this is not a conventional knowledge graph

The important relation is often not a binary relation such as "A relates to B." It is:

> These premises, under this reasoning step and scope, justify this conclusion.

This relation can have many premises and additional properties. We represent it using a first-class `ReasoningStep` node.

```text
[Statement: Drug X binds target Y] ---+
                                    +--> [ReasoningStep: extrapolation] --> [Statement: Drug X improves patient outcome]
[Statement: Target Y is implicated] -+
```

This is an argument/provenance hypergraph represented as a normal directed graph. It resolves the apparent node-versus-edge ambiguity:

- An assumption is a `Statement` used as a premise.
- A conclusion is a `Statement` selected as the target of a goal.
- A deduction or extrapolation is a `ReasoningStep` with an optional method annotation.
- Evidence is a source-backed statement, not a special logical primitive.
- A rebuttal can attack a statement or a reasoning step.

### Three layers

The model has three distinct layers.

| Layer | Contents | Purpose |
| --- | --- | --- |
| Evidence layer | Sources, excerpts, API records, datasets, tool outputs, agent messages | Lets users see exactly where information came from. |
| Argument layer | Statements, reasoning steps, support/rebuttal/qualification links, goals | The primary graph rendered in the UI. |
| Semantic layer | Optional annotations such as entities, scope, claim kind, inference risk | Enables retrieval, matching, and deterministic checks. |

Natural-language content in the evidence and argument layers remains the source of truth. Semantic annotations are versioned claims about how that content should be interpreted.

## 5. Minimal Primitives

Keep the core data model small. Do not create a permanent node type for every scientific concept.

### 5.1 Source

A source is a document or external record.

Examples:

- uploaded PDF or markdown file;
- Amass BiomedCore publication;
- Amass TrialCore trial;
- Amass DrugCore, GeneCore, or RegulatoryCore record;
- dataset;
- agent tool response;
- user-authored note.

Required fields:

```ts
type Source = {
  id: string
  kind: "document" | "amass_record" | "dataset" | "tool_output" | "agent_message" | "note"
  title?: string
  externalIds: Record<string, string>
  origin: "upload" | "amass" | "agent" | "user" | "system"
  contentHash?: string
  createdAt: string
  metadata: Record<string, unknown>
}
```

### 5.2 Excerpt

An excerpt is an immutable, navigable fragment of a source. Every factual statement should have at least one attached excerpt where feasible.

Examples:

- PDF page 4, character range 1200-1530;
- Figure 2 caption;
- table cell;
- Amass record path `outcomes[0]`;
- agent tool output fragment.

```ts
type Excerpt = {
  id: string
  sourceId: string
  text: string
  locator: {
    page?: number
    section?: string
    start?: number
    end?: number
    figure?: string
    table?: string
    jsonPath?: string
  }
  createdAt: string
}
```

### 5.3 Statement

A statement is an atomic, inspectable natural-language proposition. It may be an observation, a hypothesis, a condition, an assumption, an objection, or a conclusion.

Do not require all statements to fit subject-predicate-object form. The following are all valid statements:

- "Drug X improves outcome Y in adults with disease Z."
- "The assay calibration may introduce systematic bias."
- "If pathway P mediates the effect, knocking out gene G should remove it."
- "No causal conclusion follows from this observational dataset."
- "The sample-size calculation assumes normally distributed residuals."

```ts
type Statement = {
  id: string
  text: string
  sourceExcerptIds: string[]
  assertionMode: "asserted" | "hypothesis" | "conditional" | "question" | "reported"
  role?: "premise" | "conclusion" | "assumption" | "objection" | "definition"
  lifecycle: "proposed" | "accepted" | "superseded" | "rejected"
  createdBy: Provenance
  createdAt: string
}
```

`reported` means that the system is recording what a source or agent said, not independently asserting that it is true.

### 5.4 ReasoningStep

A reasoning step explicitly connects zero or more premises to a conclusion. It is the core logical object.

```ts
type ReasoningStep = {
  id: string
  premiseIds: string[]
  conclusionId: string
  explanation: string
  lifecycle: "proposed" | "accepted" | "superseded" | "rejected"
  sourceExcerptIds: string[]
  createdBy: Provenance
  createdAt: string
}
```

The `explanation` should be natural language. Optional semantic annotations may identify it as an extrapolation, causal inference, deduction, induction, or analogy.

### 5.5 Goal

A goal represents the question or decision currently being investigated.

```ts
type ResearchGoal = {
  id: string
  question: string
  targetStatementIds: string[]
  completionCriteria: string[]
  falsifiers: string[]
  status: "open" | "conditionally_answered" | "answered" | "abandoned"
}
```

Completion criteria are important. They state what sort of support is needed for a claim to count as addressed, such as "direct human interventional evidence with an outcome matching Y."

### 5.6 Proof obligation

A proof obligation is a system-generated or user-generated requirement that blocks acceptance of a statement or goal.

```ts
type ProofObligation = {
  id: string
  kind: "missing_direct_evidence" | "missing_premise" | "scope_mismatch" | "conflict_unresolved" | "source_invalidated" | "underspecified"
  description: string
  blocksStatementIds: string[]
  blocksGoalIds: string[]
  requiredCondition: string
  generatedByRule?: string
  status: "open" | "resolved" | "waived"
  createdAt: string
}
```

Proof obligations are not generic todos. They state why a claim is presently unearned. The agent can convert them into actions.

### 5.7 Provenance

Every generated object must record how it came to exist.

```ts
type Provenance = {
  actorType: "user" | "agent" | "extractor" | "rule_engine" | "integration"
  actorId: string
  model?: string
  promptVersion?: string
  runId?: string
}
```

## 6. Optional Semantic Annotations

Semantic annotations are optional, extensible, versioned, and reviewable. They must not erase or replace statement text.

```ts
type Annotation = {
  id: string
  subjectType: "statement" | "reasoning_step" | "source" | "excerpt"
  subjectId: string
  type: string
  value: unknown
  producedBy: Provenance
  confidence?: number
  status: "proposed" | "accepted" | "rejected"
  createdAt: string
}
```

Useful initial annotation types:

| Annotation | Example | Use |
| --- | --- | --- |
| `claim_kind` | `causal`, `association`, `methodological`, `recommendation` | Enables targeted rules. |
| `entities` | Drug X, EGFR, clinical outcome | Improves retrieval and linking. |
| `scope` | human adults, in-vitro, mouse, endpoint, dose | Detects scope leaps and contradictions. |
| `evidence_type` | RCT, observational study, simulation, review | Supports evidence requirement checks. |
| `inference_method` | extrapolation, deduction, induction, causal inference | Makes reasoning inspectable. |
| `inference_risk` | in-vitro-to-human extrapolation | Produces targeted critique. |
| `formal_pattern` | conditional antecedent/consequent | Supports future logical analysis. |
| `source_status` | retracted, corrected, superseded | Enables source invalidation checks. |

Example scope annotation:

```json
{
  "type": "scope",
  "value": {
    "population": "adults with disease Z",
    "model": "human",
    "endpoint": "clinical outcome Y",
    "conditions": ["dose=10mg", "12 weeks"]
  }
}
```

Rules should only draw a strong conclusion when their required annotations are available. If the system cannot identify necessary scope or evidence type, it raises an `underspecified` issue rather than declaring the argument valid or invalid.

## 7. Relationships in the Argument Graph

Core graph relations are deliberately limited:

| From | Relation | To | Meaning |
| --- | --- | --- | --- |
| Statement | `premise_of` | ReasoningStep | Statement is used by a reasoning step. |
| ReasoningStep | `concludes` | Statement | Reasoning step produces a conclusion. |
| Excerpt | `grounds` | Statement | Excerpt is evidence for the statement as reported. |
| Statement | `rebuts` | Statement | Statement offers contrary evidence/conclusion. |
| Statement | `undercuts` | ReasoningStep | Statement challenges an inference without necessarily asserting the opposite conclusion. |
| Statement | `qualifies` | Statement/ReasoningStep | Statement narrows conditions or interpretation. |
| Statement | `specializes` | Statement | Statement is a more specific version of another. |
| Statement | `revises` | Statement | Explicit correction/revision. Never inferred solely from similarity. |
| ProofObligation | `blocks` | Statement/Goal | Requirement prevents acceptance. |

Use an undercut when a source demonstrates a confounder, invalid method, scope mismatch, or retraction. This differs from a rebuttal, which argues for incompatible substantive content.

## 8. Deterministic Verification

### 8.1 Boundary

LLMs may:

- segment documents;
- extract candidate statements;
- propose links and annotations;
- classify likely source relevance;
- explain an issue in natural language.

The deterministic verifier must:

- validate graph schemas and references;
- apply rules over accepted annotations;
- generate reproducible issue records;
- identify the exact graph objects causing each issue;
- recompute downstream state when the graph changes.

### 8.2 Initial rules

Implement these rules first.

1. **Ungrounded statement**
   - Material statement has no source excerpt or declared assumption status.
   - Produces a proof obligation for evidence or explicit assumption labelling.

2. **Missing premise**
   - Reasoning step has an expected prerequisite that is absent or unresolved.
   - Example: a clinical efficacy conclusion lacks a direct outcome-evidence premise.

3. **Causality overclaim**
   - A statement annotated `causal` or `recommendation` is supported only by association/observational evidence.
   - Does not prove the claim false; marks its causal support as insufficient.

4. **Scope leap**
   - A reasoning step crosses material scope boundaries without an explicit bridge.
   - Examples: in-vitro to human, mouse to adult patient, biomarker to clinical outcome, a different dose/endpoint/population.

5. **Direct conflict**
   - Comparable statements have incompatible polarity under substantially matching entities and scope.
   - Keep both statements; create an unresolved conflict obligation instead of choosing a winner.

6. **Invalidated source**
   - A statement depends on a source annotated as retracted, withdrawn, or superseded.
   - Propagate impact to dependent conclusions.

### 8.3 Status presentation

Do not use an arbitrary score out of 100. For each selected claim show:

- direct evidence: present / absent / indirect;
- scope match: matched / partial / mismatch;
- independent support: present / absent;
- conflicts: absent / unresolved;
- source health: valid / corrected / retracted;
- open critical obligations.

Suggested overall statuses:

- Directly supported in scope
- Supported with open assumptions
- Contested
- Unsupported
- Blocked by invalidated evidence

The status is a derived view, not a permanent truth label stored on the statement.

## 9. Append-Only Events and Revisions

The graph must support later discoveries that change the interpretation of earlier material.

Examples:

- a later section supplies a missing justification;
- a new source refutes a previous premise;
- a user corrects an extraction;
- Amass reports that a publication was retracted;
- an agent changes its hypothesis.

Never mutate historical graph objects silently. Persist append-only events and derive current state.

```text
SourceCreated
ExcerptCreated
StatementProposed
StatementAccepted
ReasoningStepProposed
AnnotationAccepted
RelationCreated
IssueRaised
IssueResolved
ObligationCreated
ObligationResolved
StatementSuperseded
SourceStatusUpdated
```

Each event produces a validated `GraphPatch`. A materialized current graph is maintained for fast reads, while event history supports auditability, replay, and evaluation.

### Incremental recomputation

When a graph patch is accepted:

1. identify changed statements, reasoning steps, annotations, and source status;
2. traverse their downstream dependency cone;
3. rerun deterministic rules only for that affected subgraph;
4. update derived issues, obligations, and claim statuses;
5. emit an event to the frontend and relevant agent session.

Do not recompute the entire workspace for every update.

## 10. Retrieval and Graph Resolution

Append-only updates require reliable placement of new information. Retrieval is not optional.

### 10.1 Exact identity retrieval

Use strong identifiers first:

- Amass IDs: `AMBC_*`, `AMTC_*`, `AMDC_*`, `AMGC_*`, `AMRC_*`;
- PMID, PMCID, DOI, NCT ID, ChEMBL ID, gene symbol;
- document content hash;
- source/excerpt location.

Exact identifiers prevent duplicate external-source nodes.

### 10.2 Structured claim retrieval

Where available, index accepted semantic annotations:

- normalized entities;
- predicate/relation text;
- polarity;
- claim kind;
- population/model/endpoint;
- evidence type.

This finds likely duplicates, conflicts, specialisations, and related claims more reliably than semantic similarity alone.

### 10.3 Hybrid semantic retrieval

Use full-text search plus embeddings over:

- statement text;
- excerpts;
- source title/abstract/full text when available;
- reasoning explanations;
- open obligations.

For a new claim, retrieve a bounded candidate set using:

1. exact IDs and entities;
2. lexical/full-text match;
3. vector similarity;
4. current goal and active component priority.

An LLM may classify candidates as `same`, `specializes`, `generalizes`, `supports`, `rebuts`, `qualifies`, or `unrelated`. The system must preserve both statements and require an explicit typed relation. It must not automatically merge semantically similar claims.

### 10.4 Agent graph tools

Agents should never invent graph IDs or operate over the whole graph blindly.

```text
graph.search(query, entity_filters, scope_filters)
graph.get_context(node_ids, depth)
graph.get_open_obligations(goal_id)
graph.propose_patch(patch)
graph.validate_patch(patch)
research.check(goal_id, proposed_action_or_conclusion)
```

`graph.propose_patch` can create new objects and link them to existing IDs. The server validates schema, source anchoring, references, and relation types before appending the patch.

### 10.5 Context packets

Never inject the whole graph into an agent prompt. Build a compact context packet containing:

- active goal;
- selected target statement;
- local upstream/downstream subgraph;
- relevant source excerpts;
- accepted annotations;
- open critical obligations;
- conflicts;
- allowed next modes: retrieve, analyze, narrow claim, propose experiment, abstain.

## 11. Agent Guardrail Protocol

### Static research contract

Every compatible agent should receive a stable instruction similar to:

```text
You operate against an evidence graph.

Before stating a material conclusion:
1. Register the conclusion and its scope.
2. Attach source-backed premises and declare each reasoning step.
3. Query the verifier for unresolved proof obligations.
4. If critical obligations remain, resolve them, narrow the conclusion,
   or report the conclusion as conditional.

Do not treat association as causal evidence or transfer evidence across
populations/models without recording an extrapolation.
```

### Dynamic loop

```text
1. Agent sets a research goal and desired target claim.
2. Agent queries Amass or other sources and records source-backed statements.
3. Agent proposes reasoning steps and graph links.
4. Backend validates and appends the graph patch.
5. Verifier updates local status and proof obligations.
6. Agent receives a context packet.
7. Planning agent chooses a next action or narrows its conclusion.
8. Agent finalizes only when the graph state supports its stated level of certainty.
```

### Agent planning

The agent is free to propose target claims and intended support relations. The verifier enforces requirements after a relation is declared.

Example:

```text
Agent target claim:
Drug X improves outcome Y in adult humans.

Verifier state:
Only in-vitro binding and mouse evidence exists.

Proof obligation:
Direct human intervention/outcome evidence is absent.

Agent may choose:
- query Amass TrialCore for completed interventional trials;
- retrieve linked result publications;
- narrow the conclusion to a mechanistic hypothesis;
- propose a discriminating study;
- abstain.
```

## 12. Amass Integration

Amass is a key sponsor integration and a high-quality structured source layer. It is not a truth oracle; its records become evidence objects with provenance.

Relevant cores:

- **BiomedCore:** publications, abstracts, full text when available, references, cited-by links, retraction state.
- **TrialCore:** study type, phase, status, eligibility, endpoints, outcome data, linked publications.
- **DrugCore:** molecules, mechanisms of action, targets, clinical stage, linked trials/literature.
- **GeneCore:** gene metadata, target intelligence, safety and essentiality information.
- **RegulatoryCore:** FDA/EMA authorization status and indications.

Useful query path:

```text
DrugCore -> target GeneCore -> relevant BiomedCore papers
         -> TrialCore trials/results -> RegulatoryCore authorization
```

This creates concrete checks:

- a molecular mechanism is not clinical efficacy;
- a trial record is not automatically a positive outcome;
- clinical efficacy is not regulatory approval;
- a regulatory indication is scoped to a condition/population;
- a retracted publication invalidates dependent support.

Implementation notes:

- Amass is called only by the backend; never expose the API key to the browser.
- Cache Amass records by canonical Amass ID.
- Respect the documented 60 requests/minute default rate limit.
- Use Amass lookup endpoints to resolve PMIDs, DOIs, NCT IDs, ChEMBL IDs, and gene identifiers to canonical IDs.
- Persist source snapshots and timestamps so the demo remains reproducible.

## 13. Backend Architecture

### Services

```text
Browser frontend
  |
  | REST + WebSocket
  v
API service
  |- authentication/workspaces
  |- source upload and source query
  |- graph query and graph patch validation
  |- verifier endpoints
  |- MCP server endpoints
  |
  +--> Worker service
  |      |- PDF/markdown parsing
  |      |- LLM extraction
  |      |- annotation/link proposals
  |      |- verification/recomputation
  |
  +--> Amass adapter
  |
  v
PostgreSQL + Redis
```

### Recommended stack

- **API:** Python FastAPI.
- **Validation/schema:** Pydantic.
- **Background queue:** Redis plus ARQ or Dramatiq. Choose one; do not introduce Celery for the hackathon.
- **Database:** PostgreSQL.
- **Vector search:** pgvector extension in PostgreSQL.
- **LLM calls:** OpenRouter with provider/model chosen in environment configuration.
- **Structured LLM output:** JSON Schema/Pydantic response validation and retry/repair on invalid output.
- **Document parsing:** PDF text extraction plus page/character locators. Preserve original upload even if parsing is imperfect.
- **Object storage:** mounted Docker volume for MVP. Add MinIO only if needed.

### Why not Neo4j initially

The MVP graph is revision-heavy, audit-oriented, and small enough for PostgreSQL. Postgres gives transaction safety, JSONB metadata, full-text search, pgvector, backups, and simple Docker deployment. Use adjacency queries and recursive CTEs for local dependency traversal. Revisit a dedicated graph database only after profiling demonstrates a real need.

## 14. Database Outline

Core tables:

```text
workspaces
sources
excerpts
statements
reasoning_steps
graph_edges
annotations
research_goals
proof_obligations
issues
graph_events
agent_runs
agent_messages
amass_cache
```

Important indexes:

- exact IDs: Amass IDs, DOI, PMID, PMCID, NCT, ChEMBL, gene identifiers;
- source content hash;
- statement full-text search;
- excerpt full-text search;
- statement/excerpt embedding vector indexes;
- normalized entity and annotation fields;
- graph edge source/target indexes;
- active workspace and lifecycle-status indexes.

Use UUIDs or ULIDs for internal IDs. Every object belongs to a workspace and has a created-at timestamp.

## 15. API and MCP Surface

### REST endpoints

Initial endpoint groups:

```text
POST   /workspaces
GET    /workspaces/{id}

POST   /workspaces/{id}/sources
GET    /sources/{id}
GET    /sources/{id}/excerpts/{excerptId}

GET    /workspaces/{id}/graph
POST   /workspaces/{id}/graph/patches
POST   /workspaces/{id}/graph/search
GET    /workspaces/{id}/graph/context

POST   /workspaces/{id}/goals
GET    /goals/{id}/obligations
POST   /goals/{id}/check

POST   /integrations/amass/search
POST   /integrations/amass/import
```

### WebSocket events

```text
source.parse.progress
graph.patch.accepted
graph.node.created
graph.node.updated
issue.raised
issue.resolved
obligation.created
obligation.resolved
agent.run.updated
```

### MCP tools

Expose the same functionality through MCP for agents.

```text
research_goal.create(question, completion_criteria, falsifiers)
claim.record(statement, source_ids, annotations)
evidence.record(excerpt, source_metadata)
reasoning.record(premise_ids, conclusion_id, explanation, annotations)
graph.search(query, filters)
graph.get_context(node_ids, depth)
obligations.list(goal_id)
research.check(goal_id, proposed_action_or_conclusion)
graph.propose_patch(patch)
conclusion.finalize(statement_id)
```

The hackathon demo may use an in-process custom agent against these tools. Do not spend the first build phase integrating every external agent framework.

## 16. Frontend Architecture

### Framework

Use Vue 3, TypeScript, and Vite. Do not use SSR for the MVP.

This is an authenticated interactive workspace with graph canvases, PDF viewers, and real-time state. SEO is not important. A Vite SPA is faster to implement and deploy. Nuxt/SSR can be revisited if public shareable reports are added later.

### Core UI views

1. **Workspace/dashboard**
   - source list;
   - active research goals;
   - open critical obligations;
   - recent agent runs.

2. **Graph canvas**
   - interactive zoom, pan, and search;
   - default view of a single connected argument component;
   - auto-layout by dependency direction;
   - direct neighbors highlighted on selection;
   - distinct visual styles for statements, reasoning steps, evidence anchors, issues, and obligations;
   - filtering by status, source, claim type, and agent run.

3. **Statement inspector**
   - original statement text;
   - status and evidence-basis dimensions;
   - source excerpts;
   - upstream premises and downstream impact;
   - accepted/proposed annotations;
   - history/revisions;
   - open obligations and issues.

4. **Source viewer**
   - PDF.js or text view;
   - exact highlighted excerpt;
   - links back to every graph statement grounded in the excerpt;
   - source metadata, identifiers, and Amass provenance.

5. **Argument-chain view**
   - linear/tree representation for a selected conclusion;
   - clear display of AND/OR-like premise groupings;
   - issues inserted at the exact reasoning step they affect.

6. **Agent replay view**
   - timeline of agent actions, source retrieval, graph patches, and verifier responses;
   - before/after comparison for baseline versus guarded agent runs.

### Frontend libraries

- Graph rendering: Cytoscape.js.
- Layout: ELK.js if Cytoscape built-in layouts are insufficient.
- PDF viewer: PDF.js.
- State: Pinia or Vue composables.
- Realtime updates: native WebSocket client.

The graph should be attractive, but visual design must clarify reasoning rather than merely show a decorative network.

## 17. MVP Build Plan

### Phase 1: Foundation

1. Docker Compose setup with API, worker, Postgres, Redis, frontend, and Caddy.
2. Database migrations and core data models.
3. Workspace/source upload support.
4. Markdown/plain-text ingestion first; PDF ingestion after the core graph works.
5. Graph patch API with append-only event storage.
6. Basic graph query endpoint and WebSocket events.

### Phase 2: Material review flow

1. Parse a prepared small document set into excerpts.
2. Use OpenRouter structured output to propose statements, reasoning steps, and source links.
3. Validate and append extracted graph patches incrementally.
4. Build graph canvas and source inspector.
5. Implement ungrounded statement and missing-premise rules.

### Phase 3: Scientific verification and Amass

1. Add optional annotations for claim kind, evidence type, and scope.
2. Implement causality-overclaim, scope-leap, direct-conflict, and invalidated-source rules.
3. Add backend Amass search/import adapter.
4. Render Amass source cards and use canonical IDs.
5. Add proof-obligation UI and critical-path view.

### Phase 4: Agent integration and demo

1. Implement a simple custom research agent using OpenRouter.
2. Give it the static research contract and graph/MCP tools.
3. Implement `research.check` context packets.
4. Record an unguarded baseline run and a guarded run on the same scenario.
5. Build replay/side-by-side UI.

## 18. Demo Specification

Use a narrow, prepared biomedical scenario augmented by Amass records. Do not depend on unpredictable live web search during the core demonstration.

Suggested narrative:

1. Ask an unguarded agent whether a drug should be pursued for a disease outcome.
2. The agent finds molecular/preclinical evidence and a related trial, then produces a confident recommendation.
3. The system builds the argument graph live.
4. Select the recommendation node. Show its chain:
   - mechanistic evidence is direct in its own scope;
   - a reasoning step extrapolates to human clinical outcome;
   - trial evidence is absent, mismatched, incomplete, or not evidence of the stated outcome;
   - regulatory evidence does not establish the claimed indication.
5. The recommendation turns from "supported" to "blocked" with specific proof obligations.
6. The guarded agent receives the context packet and chooses a better action, such as querying TrialCore for completed interventional trials and outcomes.
7. It either retrieves suitable support or responsibly narrows its conclusion to a hypothesis.

The visual payoff should be:

- graph forms incrementally;
- a new contradiction or scope annotation visibly propagates through dependent nodes;
- the final recommendation is blocked at the exact invalid reasoning step;
- baseline and guarded agent behavior are comparable.

## 19. Evaluation Harness

Create a small replayable benchmark, not an unsupported claim of broad superiority.

Initial scenarios:

1. Correlation presented as causation.
2. In-vitro or animal evidence extrapolated to human outcome.
3. Retracted source supporting an important conclusion.
4. Direct conflicting evidence omitted.
5. Trial existence mistaken for efficacy or regulatory approval.

For each scenario, define expected critical obligations and the correct conclusion class.

Compare:

- baseline agent with the same sources but no graph/verifier;
- agent using the graph/verifier context and tools.

Measure:

- unsupported material-claim count;
- critical-evidence omission count;
- invalid causal/scope transition count;
- appropriate abstention/narrowing rate;
- source/excerpt traceability;
- whether the next action targets a critical proof obligation.

Do not claim that a weaker model universally beats a stronger model. State that the system makes research behavior more auditable and catches defined failure modes that an unguarded agent may miss.

## 20. Deployment

Deploy all services to the VPS using Docker Compose.

```text
caddy
frontend
api
worker
postgres
redis
```

Deployment requirements:

- Caddy handles HTTPS and reverse proxying for the configured domain.
- Database and Redis are private Docker-network services, not internet exposed.
- Store OpenRouter and Amass keys in a non-committed `.env` file.
- Use database migrations on deploy.
- Mount persistent volumes for Postgres and uploaded source files.
- Add basic request-size limits for PDFs/uploads.
- Add authentication before public deployment if time permits; at minimum protect the demo workspace.

## 21. Team Ownership Suggestions

| Area | Deliverable |
| --- | --- |
| Backend/graph | Models, migrations, graph patch validation, event log, dependency traversal, rules. |
| Extraction/LLM | Prompt/schema design, chunking, structured output validation, annotation/link proposal. |
| Amass | API adapter, canonical-source import, caching, demo source snapshots. |
| Frontend | Graph canvas, inspector, source viewer, obligations, replay. |
| Agent/evaluation | MCP/client wrapper, research contract, baseline/guarded runs, benchmark fixtures. |
| DevOps | Docker Compose, Caddy, environment/secrets, VPS deployment. |

## 22. Decisions That Must Remain Consistent Across All Work

1. Statements and reasoning explanations are stored in natural language first.
2. Semantic annotations are optional, versioned, and never overwrite text.
3. Reasoning steps are first-class nodes, not anonymous graph edges.
4. All updates are append-only events; current state is materialized from history.
5. Sources and excerpts are immutable provenance anchors.
6. Similar statements are linked, not automatically merged.
7. The deterministic verifier creates proof obligations; the agent chooses actions.
8. Every issue must explain itself with references to specific graph objects and source excerpts.
9. Amass records are high-quality external sources, not unquestionable truth.
10. The product is model-agnostic and exposed via REST plus MCP.

## 23. Immediate Next Steps

1. Agree on this data contract before anyone builds separate models.
2. Create a minimal Compose stack and empty Vue/FastAPI applications.
3. Implement source, excerpt, statement, reasoning-step, edge, event, and obligation tables.
4. Implement graph patch validation and a manually seeded graph fixture.
5. Build the graph canvas against the seeded fixture before adding LLM extraction.
6. Add one deterministic rule: ungrounded statement.
7. Add one Amass import path and one prepared biomedical scenario.
8. Add incremental extraction and the remaining high-value rules.
9. Build the baseline versus guarded-agent replay only after the graph and verifier are visibly working.

## 24. Shared Implementation Conventions

These conventions prevent independently built components from becoming incompatible.

### Repository shape

Use a simple monorepo structure:

```text
LogicCritic/
  frontend/              Vue 3 + Vite application
  backend/               FastAPI application and database migrations
  worker/                background job definitions and consumers
  shared/                OpenAPI schema, JSON fixtures, common contracts
  infra/                 Caddy and Docker-related configuration
  fixtures/              prepared demo documents, Amass snapshots, benchmark cases
  docker-compose.yml
  .env.example
  ARCHITECTURE.md
```

The backend is the owner of all persistent contracts. The frontend and agent code must consume its generated OpenAPI types or checked-in shared JSON schemas rather than recreating their own data models.

### Required environment variables

```text
POSTGRES_URL=
REDIS_URL=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=
AMASS_API_KEY=
APP_BASE_URL=
JWT_SECRET=
UPLOAD_DIR=/data/uploads
```

`.env.example` lists variable names and safe placeholder values. `.env` is never committed. Keep OpenRouter and Amass calls in the backend/worker only.

### Background-job states

Jobs need persisted state so the frontend can recover after refreshes and so failures are understandable:

```text
queued -> running -> succeeded
                  -> failed
                  -> needs_review
```

Record the input source, model/provider, prompt version, retry count, error message, output patch ID, and timestamps. A malformed LLM response must never create a partial graph patch.

### Minimum definition of done

A feature is complete only when:

1. its API/schema is documented or generated;
2. it records provenance for created graph objects;
3. it handles retried requests/jobs without duplicate objects;
4. its errors are visible in the UI or agent response;
5. it has a fixture or test covering its intended behavior;
6. it does not bypass graph-patch validation to write persistent graph state directly.

### Compatibility test fixture

Maintain one manually authored fixture workspace containing:

- two source excerpts;
- a supported mechanistic statement;
- a human-outcome conclusion;
- an extrapolation reasoning step;
- an open scope-leap obligation;
- a direct rebuttal added in a later graph event.

Every frontend, backend, worker, and agent change should be able to load this fixture. It is the quickest check that event replay, graph rendering, source navigation, impact propagation, and context packets still agree.
