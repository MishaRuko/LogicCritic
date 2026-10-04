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
BASIC_AUTH_USER=misha
BASIC_AUTH_HASH='$2a$...'
POSTGRES_PASSWORD=...
POSTGRES_URL=postgresql://logiccritic:...@postgres:5432/logiccritic
JWT_SECRET=...
CLAUDE_API_KEY=...
AMASS_API_KEY=...
```

Generate the password hash with:

```bash
docker run --rm caddy:2.10-alpine caddy hash-password --plaintext 'your-password'
```

The single quotes around `BASIC_AUTH_HASH` prevent Compose from interpreting the hash's dollar
signs. Use a unique database password and JWT secret. Never commit `.env`.

## Start

```bash
COMPOSE_PROJECT_NAME=logiccritic-zebi docker compose up -d --build
```

Apply `infra/nginx-zebi.conf` to the host Nginx configuration only after the loopback service is
healthy. Validate Nginx before reloading it.

```bash
curl --user "$BASIC_AUTH_USER:$BASIC_AUTH_PASSWORD" http://127.0.0.1:8088/api/health/ready
sudo cp infra/nginx-zebi.conf /etc/nginx/sites-available/zebi.misharuko.com
sudo ln -s /etc/nginx/sites-available/zebi.misharuko.com /etc/nginx/sites-enabled/zebi.misharuko.com
sudo nginx -t
sudo systemctl reload nginx
```

## Backups

Back up the Compose project's PostgreSQL and uploads volumes together. A database-only backup does
not contain uploaded source files. Test restoration before relying on the deployment for durable
research data.
