# Production deployment

The production stack is designed to run as its own Docker Compose project behind a host reverse
proxy. It must not publish its ingress on a public interface.

## Required environment

Set these values in an untracked `.env` file:

```dotenv
APP_ENV=production
APP_BASE_URL=https://zebi.misharuko.com
APP_BIND=127.0.0.1
APP_PORT=8088
CADDYFILE=./infra/Caddyfile.production
POSTGRES_PASSWORD=...
POSTGRES_URL=postgresql://logiccritic:...@postgres:5432/logiccritic
JWT_SECRET=...
CLAUDE_API_KEY=...
AMASS_API_KEY=...
```

Use a unique database password and JWT secret. Never commit `.env`. This configuration is public;
add authentication or another access control before running it as a long-lived deployment.

## Start

```bash
COMPOSE_PROJECT_NAME=logiccritic-zebi docker compose up -d --build
```

Apply `infra/nginx-zebi.conf` to the host Nginx configuration only after the loopback service is
healthy. Validate Nginx before reloading it.

```bash
curl http://127.0.0.1:8088/api/health/ready
sudo cp infra/nginx-zebi.conf /etc/nginx/sites-available/zebi.misharuko.com
sudo ln -s /etc/nginx/sites-available/zebi.misharuko.com /etc/nginx/sites-enabled/zebi.misharuko.com
sudo nginx -t
sudo systemctl reload nginx
```

## Backups

Back up the Compose project's PostgreSQL and uploads volumes together. A database-only backup does
not contain uploaded source files. Test restoration before relying on the deployment for durable
research data.

## Blind agent evaluation

The backend can run a small stored, blind comparison of the same Sonnet model with and without
LogicCritic's guardrails. It is intentionally not exposed through the public UI. Each source packet
is copied into two isolated workspaces, so the guarded graph cannot leak into the baseline run.

Create a JSON source packet containing full text you are permitted to use:

```json
{
  "id": "paper-set-01",
  "sources": [{"title": "Paper title", "text": "# Full paper text\n..."}]
}
```

For open-access Europe PMC articles, create reproducible packets directly from PMCIDs. The importer
stores the abstract and article body (references omitted) and caps each packet at 80,000 characters
to preserve the pilot's cost ceiling:

```bash
docker compose exec api python -m app.evaluations.cli fetch-pmc --output-dir /data/eval-packets PMC11573799
```

Generate a few adversarial cases with Opus, review only for obvious malformed input, then persist
the immutable manifest before spending on agent runs:

```bash
docker compose exec api python -m app.evaluations.cli generate --packet /data/packet.json --output /data/cases.jsonl --count 3
docker compose exec api python -m app.evaluations.cli create --name "full-text pilot" --manifest /data/cases.jsonl
docker compose exec api python -m app.evaluations.cli run <evaluation-id> --concurrency 5
docker compose exec api python -m app.evaluations.cli report <evaluation-id>
```

`run` is resumable (finished arms and scores are kept; `--retry-failed` re-runs failed arms).
Two further entry points:

```bash
# SciFact dev claims with expert gold labels (SUPPORTS / CONTRADICTS / NOT ENOUGH INFO)
docker compose exec api python -m app.evaluations.cli scifact --name "scifact" --per-label 20
# Re-score stored answers with the current scorer, without re-running any agent
docker compose exec api python -m app.evaluations.cli score <evaluation-id>
```

Neither arm sees the evaluator's rubric. Scoring (scorer `v3`) is length-neutral: each answer is
scored alone against the source packet, never beside the other arm's answer. Opus derives a key
per case (expected verdict and 1-3 required deductions; SciFact uses its gold label) and then
reports, per answer, every factual claim as supported / contradicted / unsupported, invalid
inferences, the verdict given (the expected verdict is withheld from the scorer) and which
required deductions were drawn. Omissions are never errors. The report gives arm means and the
guarded-minus-baseline difference with a paired bootstrap 95% CI. Apart from SciFact's labels,
keys and scores come from an LLM: a hackathon signal, not independent scientific validation.
