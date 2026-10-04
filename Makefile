.PHONY: up down frontend backend test test-e2e
up:
	docker compose up -d --build

down:
	docker compose down

backend:
	docker compose -f docker-compose.yml -f compose.dev.yml up -d postgres redis api worker replay-worker

frontend:
	pnpm --dir frontend dev

test:
	pnpm --dir frontend test
	pnpm --dir frontend typecheck
	pnpm --dir frontend build

test-e2e:
	pnpm --dir frontend test:e2e
