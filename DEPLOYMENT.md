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

Generate a few adversarial cases with Opus, review only for obvious malformed input, then persist
the immutable manifest before spending on agent runs:

```bash
docker compose exec api python -m app.evaluations.cli generate --packet /data/packet.json --output /data/cases.jsonl --count 3
docker compose exec api python -m app.evaluations.cli create --name "full-text pilot" --manifest /data/cases.jsonl
docker compose exec api python -m app.evaluations.cli run <evaluation-id>
docker compose exec api python -m app.evaluations.cli report <evaluation-id>
```

The run persists source packets, case rubrics, arm-to-blind-label mappings, all agent outputs and
usage, anonymous judge material/verdicts, and the generated Markdown report. Opus sees answers as
only `A` and `B`; the revealed arm mapping is kept in the database/report. This is a useful blinded
hackathon signal, not independent scientific validation: the stronger model both creates cases and
judges them.
