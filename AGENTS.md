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

Current baseline: backend 413 passed, frontend 91 passed, vision 77 passed.

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
Scholar's keyless pool is shared and often answers 429; OpenAlex (about 100 free searches a day)
answers instead. `SEMANTIC_SCHOLAR_API_KEY` (free, by application) raises that limit.
`read_paper` uses Amass full text, then arXiv or an open-access PDF, then the abstract only.
Tests never reach these indexes (`tests/conftest.py`), and evaluations switch them off.

Planned: Google Scholar through SerpAPI (paid; the user has had good results with it), as one
more optional source behind a `SERPAPI_KEY`. Do not use the `scholarly` scraper: Google blocks
it from servers. Scholar returns snippets only, so its hits still need full text from elsewhere.

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
finalisation). Re-run before quoting new numbers.

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

The API runs `alembic upgrade head` on start. Back up first:
`docker exec logiccritic-trial-postgres-1 pg_dump -U logiccritic logiccritic | gzip > ~/backups/trial-$(date +%F).sql.gz`.
Changing nginx needs `sudo` (password). The old `logiccritic-zebi_*` volumes and a database
backup from 4 Oct 2026 are kept on the server as a fallback.

## Known issues worth fixing

- Stale-run recovery marks agent runs failed after 5 minutes without a heartbeat; one slow model
  call or a long `check_conclusion` can trip it.
- `hypothesis` certainty is always allowed, even when the evidence rests on a retracted source.
- `required_premise` annotations from the critic can never be marked satisfied, so a flagged step
  only clears if it is rejected.
- `ungrounded_statement` only checks proposed claims: accepting an ungrounded claim hides it.
- Extraction does not create strength or causal annotations, so the causality, scope and conflict
  rules only fire on agent or manual claims.
- Lab results come back into the graph as separate claims, not linked to the claims they test.
- The agent's `record_protocol` creates a new source, which changes the research fingerprint and
  asks for verification again before an experiment.
- No authentication on the deployed site.
