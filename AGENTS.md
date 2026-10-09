# Working on Trial

Notes for anyone (person or coding agent) picking this project up in a new session. Read this
first. `ARCHITECTURE.md` is the original
design and is partly aspirational: anything it describes that is not in the code (for example
embeddings and vector search) was never built.

## What it is

Trial turns research (uploaded papers, or a research agent's own findings) into an argument
graph: claims grounded in exact source excerpts, reasoning steps from premises to conclusions,
cross-source links, and the gaps a verifier finds. A guarded research agent builds on that graph
and may only state the certainty its evidence supports. Verified research can produce a lab
protocol, and a lab video can then be checked against that protocol.

Live: https://trial.misharuko.com (no login; anyone with the link can use it and spend API credit).

## Layout

```
backend/            FastAPI app, workers, Alembic migrations (Python 3.13)
  app/agent/        research agent: loop, tools, guardrail, prompts, assurance
  app/services/     extraction, verification, judge, synthesis, graph query/Q&A, experiments
  app/evaluations/  paired baseline-vs-guarded evaluation harness and scorer
  app/routes/       HTTP API (all under /api)
  tests/            pytest suite (uses the real Postgres in Docker)
frontend/           Next.js app (read frontend/AGENTS.md: the Next version has breaking changes)
vision/             lab_vision package: protocol structuring and lab video analysis
infra/              Caddyfiles and the host nginx site (nginx-trial.conf)
fixtures/           sample material, evaluation PMCID list
docs/               experiments.md, integration.md
```

Services (docker-compose.yml): `caddy` (ingress), `frontend`, `api`, `worker` (extraction jobs,
agent runs, video experiment runs, live camera sessions), `replay-worker` (demo and replay experiment runs; without it
those stay queued), `postgres` (pgvector image, but no vectors are used), `redis`.

## Commands

```sh
cp .env.example .env            # set CLAUDE_API_KEY; AMASS_API_KEY for biomedical paper search
make up                         # full stack at http://localhost
make backend && make frontend   # dev: API in Docker, frontend on :5173
```

Backend tests (run inside the API image; install dev extras first):

```sh
AMASS_API_KEY= CLAUDE_API_KEY= docker compose run --rm --no-deps --user root \
  -v "$PWD/backend:/app" api sh -c "pip install -e '.[dev]' >/dev/null && python -m pytest -q"
```

- Blank both API keys. With real keys in the environment some tests make live calls and fail
  (the critic flags steps, Amass answers searches).
- Do not run pytest in parallel (`-n`): tests share one database and interfere.
- Schema check: `docker compose run --rm --no-deps -v "$PWD/backend:/app" api alembic check`.

Frontend: `pnpm --dir frontend test`, `pnpm --dir frontend typecheck`, `pnpm --dir frontend build`.
Vision: run `python -m pytest` in `vision/` (install `vision[dev]`).

Current baseline: backend 475 passed, frontend 104 passed, vision 80 passed.

## Rules that are easy to break

- **The graph is additive.** New material and agent runs add claims, steps and links; they do not
  edit or delete what is there. Disagreement is a new claim plus a `rebuts` link. Exceptions:
  people review (accept/reject) nodes, and the agent's `revise_claim` can withdraw claims
  recorded by an agent (any run), never uploaded or extracted ones.
- **Strict tools are capped.** The Claude API compiles all `strict` tool schemas into one grammar
  and rejects requests where it is too large ("compiled grammar is too large"). Only
  `STRICT_TOOLS` in `backend/app/agent/loop.py` are strict. A new agent tool must not be added to
  that set unless it has nested arguments, and the test `test_tools_are_closed_and_the_complex_ones_are_strict`
  pins the set. Check any change with one real API call.
- **Opus does not accept forced tool choice.** Evaluation calls to Opus use JSON-in-text
  (`structured_call(..., force_tool=False, allow_text_json=True, use_tools=False)`).
- **Never show the evaluation rubric to an arm.** `_arm_payload` sends only the question.
- **Excerpts are at most 1,500 characters** with exact offsets into the stored source. Judges and
  verifiers see cited excerpts, so a giant excerpt silently breaks verification (this happened).
- **Tests use the live Docker database.** A test that opens connections should dispose the engine
  at the end (`await engine.dispose()`), or the next test file fails with event-loop errors.

## Paper search

The agent's `search_papers` queries Amass (biomedical, needs `AMASS_API_KEY`), Semantic Scholar
(all fields) and arXiv at once and merges duplicates (`app/services/literature.py`). Semantic
Scholar's keyless pool is shared and often answers 429; OpenAlex answers instead (about 100
searches a day without a key, about 1,000 with the free `OPENALEX_API_KEY`). After Semantic
Scholar still refuses following the retries, it is skipped for 5 minutes. `SEMANTIC_SCHOLAR_API_KEY` (see below) gives a dedicated allowance.
`read_paper` uses Amass full text, then arXiv or an open-access PDF, then the abstract only.
`follow_citations` walks the citation network from one paper (Semantic Scholar, OpenAlex when it
refuses): `references` drops methods references (`METHOD_REFERENCE`) and `cited_by` covers the
most recent ~200 citers. Both rank works sharing words with `about` first, then by citations;
by citations alone, a field's guidelines and statistics papers come first. `search_papers` takes
an optional `purpose` (overview, for, against, recent, specific), which the judge sees.
Tests never reach these indexes (`tests/conftest.py`), and evaluations switch them off.

Every call to these APIs (and Amass, and OpenAlex in `review_cases`) goes through
`app/services/http_retry.send`: a 429, 5xx or timeout is retried after 2, 5 and 10 seconds (or
the wait the service asks for, up to 30 seconds), then handed back. Claude calls are retried by
the Anthropic SDK instead. `LiteratureClient` also remembers each successful, non-empty answer
for a day. When an index still refuses, `search_papers` and `follow_citations` tell the agent to
use web search. Semantic Scholar works without a key, but keyless requests share one pool with
every keyless user and are often all refused; a key (free, from the form at
https://www.semanticscholar.org/product/api#api-key-form) goes in `SEMANTIC_SCHOLAR_API_KEY`.

Planned: Google Scholar through SerpAPI (paid; the user has had good results with it), as one
more optional source behind a `SERPAPI_KEY`. Do not use the `scholarly` scraper: Google blocks
it from servers. Scholar returns snippets only, so its hits still need full text from elsewhere.

## Research depth

Agent runs are `thorough` by default (`depth` on the run request): 40 turns, 15 web searches,
300k output tokens, a research strategy in the opening message (reviews first, counter-evidence,
recent work, rephrased searches), and a one-time guard push at finalisation if fewer than 5
sources were read or 4 searches run, another if it never called `follow_citations`, and one if
it made fewer than 3 web searches (1 for quick runs). `quick` keeps the earlier 20 turns and 10 searches with no
minimums. Settings: `agent_thorough_*` in `config.py`. A run's heartbeat is renewed every 30
seconds by a background task, so a slow model call no longer looks like a lost worker. A turn cut
off at the output limit is dropped and the agent asked to continue concisely (twice at most),
rather than failing the run.

## Certainty

A guarded answer is `established`, `supported`, `tentative` or `speculative` (runs before this
stored `conditional` and `hypothesis`; they read as Tentative and Speculative). The highest
allowed is the lowest of four ceilings, all read off the conclusion's upstream cone
(`app/agent/evidence.py`):

- soundness: no critical obligation allows established; a critical one caps at tentative; a hard
  blocker at speculative; a retracted source in the cone allows nothing (abstain);
- evidence: independent sources cited by claims in the cone (deduplicated by DOI, PMID, arXiv id
  or title; retracted sources and the agent's protocol note excluded). 3 scholarly sources, or a
  systematic review plus 2, for established; 2 for supported; 1 for tentative;
- breadth: paper searches, citation follows and web searches by all runs in the workspace, plus
  sources a person supplied. 4 including one `search_papers` with purpose `against` for
  established, 2 for supported;
- quality: the guard's judge rates the body of evidence the GRADE way (`evidence_certainty`:
  start from the designs, downgrade for risk of bias, inconsistency, indirectness, imprecision,
  publication bias); high allows established, moderate supported, low tentative, very low
  speculative. It replaced a consistency check: counting sources and searches cannot see quality,
  and the GRADE calibration run (a8119910) put low-certainty evidence a level too high. Without a
  judge (tests), quality does not cap.

Replaying only the judge on run a8119910's stored material (about $0.70 a time; script kept out of
the repo, it reads `judge_verdicts.material` and the last `check` event) compared variants with
the reviewers' GRADE ratings: the holistic rating 10/18 exact and 17/18 within one level (kept);
the same with a stricter very-low rule 9/18; asking for the start level and each downgrade and
adding them up in code 5/18, and 4/18 even when told to reproduce the cited reviews' ratings (the
judge then lists every concern). One case either way is within the judge's run-to-run noise.
The judge still rates most very-low evidence as low, and open critic flags hold some
moderate-evidence answers at tentative.

Sources on government and international-body hosts (`OFFICIAL_HOST`: .gov, who.int, europa.eu,
oecd.org and so on) count as `official`, strong evidence like a paper.

A source read but not connected to the conclusion counts for nothing. `check_conclusion` returns
`certainty_ceiling` with `to_raise` (what would raise it), and the report ends with a
"Certainty:" line. The thresholds are first guesses, to be tuned against the open-search
evaluation. Tests of the verifier's rules use the `ample_evidence` fixture so only obligations
limit certainty; the ceiling itself is tested in `tests/test_agent_evidence.py`.

The critic's `required_premise` flag on a step clears when the step is replaced by a revision
(`revises_step_id`) or a person accepts the step on review. Accepting a claim never hides that it
has no source: `ungrounded_statement` checks every claim that is not rejected.
A causal conclusion reached by reasoning (no cited text of its own) is not sent to the judge for
its design; a rule requires one of its premises to have a causal design instead. The critic lets a
synthesis step state conflict, uncertainty and caveats without more support, and flags only what
it adds beyond its premises. Certainty is computed, not chosen: `finalize` gives the conclusion
the highest allowed level unless the agent passes `lowered_because`, which the report shows
("The evidence allowed X; lowered because ..."). Told to finalize at the ceiling, the agent still
chose a level lower in 3 of 15 runs, so the choice was taken away.

## Methodology for experiments

The protocol an experiment is checked against is compiled by the research agent
(`record_protocol`) for the question from all the sources it read: each step cites the passages
that state it, from any source, and where sources differ the step gives the alternatives and the
basis says what was chosen. The Experiments tab shows it as "Compiled by the research agent". If a
workspace has none (uploaded papers only, or research from before), "Compile from all sources"
starts a quick research run (no web search) that compiles one from the workspace's sources.
"Use one paper's methods instead" (the default when nothing is compiled) takes the steps from
one paper's methods section; sources without one (abstract-only papers, web pages) are listed
but cannot be chosen. Prose methods are structured by `lab_vision.structuring`, which keeps only
physical bench actions, in order, each quoting the paper verbatim.
When a paper describes alternative procedures (two device types, say), each step carries a
`variant` and the Experiments tab asks which one is being recorded; a run stores it
(`result.variant`) and is checked against the shared steps plus that procedure's
(`Protocol.only`). Characterisation steps (XRD, microscopy, electrical tests) are marked
optional, and units such as "oC" read as °C. Checked on a real fabrication paper (two
device types, 13k characters of prose methods): 32 bench steps, both procedures found.

## Models (backend/app/config.py)

| Setting | Default | Used for |
|---|---|---|
| `claude_model` | claude-sonnet-5 | extraction, argument critic, cross-source synthesis |
| `agent_model` | claude-sonnet-5-5 | research agent, graph Q&A |
| `agent_judge_model` | claude-sonnet-5 | the guard's independent judge (criteria, evidence, designs, link audits) |
| `eval_generator_model` | claude-sonnet-5-5 | the model under test in both evaluation arms |
| `eval_judge_model` | claude-opus-5-5 | evaluation case generation, scoring keys, answer scoring |
| `vision_model` | claude-opus-5-5 | lab video analysis |

## Evaluations

CLI: `python -m app.evaluations.cli` with `fetch-pmc`, `generate`, `create`, `run <id>`
(resumable, `--concurrency`, `--retry-failed`), `score <id>` (re-score stored answers),
`report <id>`, `scifact`, `paper-pilot`. See `DEPLOYMENT.md` for examples
and the scorer in `backend/app/evaluations/scoring.py` for the method.

Stored runs are in the **local** Docker database (not production):

| Evaluation | ID |
|---|---|
| First pilot (invalid: rubric leak, see docs) | d332f94d-8a1e-40a3-bc03-3eb2a695aabc |
| Fixed pilot v2, 12 cases (re-scored with v3) | 266def59-8511-4122-82be-59e8aa477051 |
| Paper pilot v3, 30 cases | e03a5b6b-462a-498b-8395-e7a352616e17 |
| SciFact dev, 60 claims | d0b352ba-9f55-4a35-8a77-0838db84d85d |

The agent changed after these runs (graph tools, `link_claims`, the protocol step at
finalisation, paper search, research depth). Re-run before quoting new numbers. Those stored runs
are no longer in the local database either.

The commands above hand each arm a source packet and switch search off: they measure reasoning
over given text. **Open-search evaluations** (`app/evaluations/open_search.py`) start each arm in
an empty workspace with search on and measure finding evidence: key papers found and read (the
cited review's most cited references), agreement with the review's conclusion (one Opus call per
answer), effort, calibration by certainty level, and consistency across repeats. Default arms are
`quick` and `thorough` (both guarded). Cases: `fixtures/evaluations/open_cases_v1.jsonl`.
`fixtures/evaluations/grade_cases_v1.jsonl` adds 18 cases rated with GRADE by Cochrane and Campbell
reviews (`expected_certainty`); the report then compares the agent's certainty with the
reviewers' (`grade_section`). That tests calibration, which agreement cannot at 97%.

```sh
python -m app.evaluations.cli review-cases <review DOIs> --output raw.jsonl   # free; write questions by hand
python -m app.evaluations.cli open-create --name ... --manifest ../fixtures/evaluations/open_cases_v1.jsonl --repeats 2
python -m app.evaluations.cli open-run <id>      # paid
python -m app.evaluations.cli report <id>
```

Each case's reference review is withheld from the agent during a run (`hidden_source` in the
run's budgets: dropped from paper search and citation results, refused by `read_paper` and
`fetch_url`). Web search is the API's server tool and cannot be filtered, so it can still list
the review, but it cannot be read. `read_the_reference` in the report counts any leak.
`open-run` runs 2 slots at a time by default: 4 exhausted the keyless Semantic Scholar pool and
then OpenAlex's daily allowance.

Open-search runs (local database):

| Evaluation | ID | Notes |
|---|---|---|
| Pilot v1, 3 questions | b1bbad0a-2773-4e40-ac9d-454335875db3 | before earned certainty |
| Pilot v2, 3 questions | c900a378-d204-4a91-878c-7228b2fb56e7 | earned certainty, citations, purpose |
| Pilot v3, 3 questions | 03652d2f-a8d6-4c4b-b0a3-9ca560d91b22 | fair verifier, citation hints |
| Open search v1, 15 x 2 | 8892b83f-71f6-4db0-bc6f-1219b8f4c563 | $39; see below |
| Clean baseline, thorough, 15 x 1 | 6a3b6eb0-f743-4f53-a4fc-b90e4ad220fc | $12.63; reference hidden, search healthy |
| Web + yes/no consistency, 15 x 1 | e7261326-d4f0-407e-a16a-eb3ac00dac7e | $13.40; judge called all 15 conflicting |
| Three-way consistency, 15 x 1 | 47f383fa-6941-48e1-9de7-7c10def91051 | $14.94; 97% agreement, 10 contested, 5 resolved |
| GRADE calibration v1, 18 x 1 | a8119910-a7bd-49d0-b315-4cb1ce3f8740 | $15.67; 7/18 same level as GRADE, 16/18 within one |

Open search v1 (9 Oct 2026) is not a clean measurement, for three reasons since fixed or noted:
paper search was throttled in 36 of 60 runs (concurrency 4, no Semantic Scholar key); half the
runs read the reference review (agreement 93-96% then, 68-72% otherwise); and all four
disagreements were case 4, whose reference was by the theory's authors (replaced). Agreement was
~81% for both arms and flat across certainty levels; thorough cost $0.88 an answer, quick $0.40.
The citation push worked (29/30 thorough runs followed citations). Re-run before quoting.

The clean baseline (10 Oct 2026, thorough only) is the first trustworthy number: 15/15
answered, no reference read, no search failures, agreement 80% (9 agree, 6 partly, 0 disagree),
$0.83 an answer. The "partly" verdicts are differences of weighting or emphasis (often the answer
calls the evidence more mixed than the review). Certainty barely varied (12 of 15 Supported), so
calibration is still untested. Web search: a median of 1 per run.

After three-way consistency (same 15 questions): agreement 97% (14 agree, 1 partly), web searches
a median of 3 (the minimum), no answer Established although 3 were allowed it (hence computed
certainty). At this agreement level the evaluation cannot test calibration: that needs harder
cases, such as questions where the evidence changed recently or where popular belief and the best
review disagree.

## Deployment

Server `misha-server`, checkout `~/logiccritic-trial`, Compose project `logiccritic-trial`,
loopback `127.0.0.1:8088`, host nginx site `trial.misharuko.com` (from `infra/nginx-trial.conf`,
Cloudflare origin certificate). Settings are in `.env` and `.env.deploy` on the server.

```sh
ssh misha-server
cd ~/logiccritic-trial && git pull --ff-only
docker compose --env-file .env --env-file .env.deploy up -d --build
curl -H "Host: trial.misharuko.com" http://127.0.0.1:8088/api/health/ready
```

The API runs `alembic upgrade head` on start, then `API_WORKERS` (default 4) uvicorn processes,
each with a database pool of `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` (5 + 10 in compose), which keeps
all processes under Postgres's default 100 connections. The workspace screen polls
`GET /workspaces/{id}/snapshot` and `/experiments`; both send an ETag and answer 304 while
nothing changed, so an idle tab costs almost nothing. Back up first:
`docker exec logiccritic-trial-postgres-1 pg_dump -U logiccritic logiccritic | gzip > ~/backups/trial-$(date +%F).sql.gz`.
Changing nginx needs `sudo` (password). The old `logiccritic-zebi_*` volumes and a database
backup from 4 Oct 2026 are kept on the server as a fallback.

## Known issues worth fixing

- Extraction records claim strength and study design (so the causality rule applies to papers),
  but not `claim_key` or scope annotations: the direct-conflict and scope-leap rules still fire
  only on agent or manual nodes. Cross-source disagreement reaches the guard through synthesis
  `rebuts` links instead.
- Lab results come back into the graph as separate claims, not linked to the claims they test.
- The agent hardly uses web search (median 1 per run of 10-15 allowed), and does not fall back to
  it when the paper indexes are throttled.
- The agent's `record_protocol` creates a new source, which changes the research fingerprint and
  asks for verification again before an experiment.
- No authentication on the deployed site.
