# Leave Management AI Assistant — developer entry points (the contract; CI mirrors these targets).
SHELL := /bin/bash
COMPOSE := docker compose -f deploy/compose.yaml
COMPOSE_DEV := $(COMPOSE) -f deploy/compose.dev.yaml
UV := uv
NPM := npm

.PHONY: help setup env lint test-unit test-integration test-e2e test-ocr contract build up up-staged up-dev down ps logs smoke stats migrate seed bootstrap rollover models ingest jobs reindex rescan chat-smoke chat-perf retention-scan security-check token precommit clean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

env: ## create deploy/.env from the example if missing; generate the M5 encryption keys when empty
	@test -f deploy/.env || (cp deploy/.env.example deploy/.env && echo "created deploy/.env — review the values")
	@python3 scripts/ensure_keys.py deploy/.env

setup: env ## install backend (uv) and frontend (npm) dependencies
	cd backend && $(UV) sync --all-groups
	cd frontend && $(NPM) ci

lint: ## ruff, ruff format, mypy, import-linter, eslint, tsc
	cd backend && $(UV) run --no-sync ruff check . && $(UV) run --no-sync ruff format --check . && $(UV) run --no-sync mypy && $(UV) run --no-sync lint-imports
	cd frontend && $(NPM) run lint && $(NPM) run typecheck

test-unit: ## backend pytest (with coverage) and frontend vitest
	cd backend && $(UV) run --no-sync pytest
	cd frontend && $(NPM) run test

test-integration: ## integration + ml + security suites against the running stack (postgres 127.0.0.1:5432, keycloak :8081, api via :8080)
	set -a && . ./deploy/.env && set +a && cd backend && $(UV) run --no-sync pytest -m "integration or keycloak or ml or scenario" --no-cov

test-ocr: ## parse the scanned Greek fixture with Docling + Tesseract inside the api container
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.documents.parse_check /app/src/leave_assistant/seed/corpus/files/aegean/diadikasia-anarrotikis.pdf

test-e2e: ## Playwright smoke against the running stack (make up-staged && make bootstrap first)
	set -a && . ./deploy/.env && set +a && cd frontend && $(NPM) run --silent test:e2e

contract: ## regenerate docs/api/openapi.json and the frontend types; fail if either changed
	cd backend && $(UV) run --no-sync python -m leave_assistant.openapi > ../docs/api/openapi.json
	cd frontend && $(NPM) run --silent api:types
	git diff --exit-code -- docs/api/openapi.json frontend/src/api/schema.d.ts

build: env ## build the api and frontend images
	$(COMPOSE) --profile core build

up: env ## start the core profile (needs ~4 GB free RAM)
	$(COMPOSE) --profile core up -d --wait

up-staged: env ## low-memory hosts: infra first, worker embedded in the api process (doc 02 §10); api limit 3 GB for the ingestion child
	$(COMPOSE) --profile core up -d --wait postgres seaweedfs mailpit
	$(COMPOSE) --profile core up -d --wait keycloak
	WORKER_MODE=embedded API_MEMORY_LIMIT=3G $(COMPOSE) --profile core up -d --wait --force-recreate api frontend   # worker container not started; loop runs inside api

up-dev: env ## core profile with hot reload and api published on :8000
	$(COMPOSE_DEV) --profile core up -d --wait

down: ## stop everything (volumes are kept; add -v manually to wipe data)
	$(COMPOSE) --profile core down

ps: ## service status
	$(COMPOSE) --profile core ps

logs: ## follow logs (SERVICE=api to filter)
	$(COMPOSE) --profile core logs -f $(SERVICE)

migrate: ## apply database migrations (inside the api container, as app_owner), then the LangGraph checkpoint tables (as langgraph_rw)
	$(COMPOSE) --profile core exec -T api alembic upgrade head
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.agent.checkpoint

seed: ## seed the two fictional companies into Keycloak and the database (idempotent)
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.seed

bootstrap: migrate seed ## first start: migrate, then seed

rollover: ## run the leave rollover now (statuses, year grants, carry-over, expiry) for all tenants
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.leave.rollover

models: ## download the embedding and Docling models into the models volume (once; ~1 GB)
	$(COMPOSE) --profile core exec -T -u root api chown -R app:app /models   # volumes created before M4 belong to root
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.infrastructure.ml.prefetch

ingest: ## process every queued document job now (drains the queue inside the api container), then show the status table
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.worker --drain
	$(MAKE) --no-print-directory jobs

jobs: ## job queue and document version status overview
	$(COMPOSE) --profile core exec -T postgres psql -U postgres -d leave -c "SELECT status, type, count(*) FROM platform.jobs GROUP BY 1, 2 ORDER BY 1, 2" -c "SELECT t.slug, d.title, v.version_no, v.status, v.pages, v.language, (SELECT count(*) FROM app.document_chunks c WHERE c.version_id = v.id) AS chunks, left(coalesce(v.processing_error, ''), 60) AS error FROM app.document_versions v JOIN app.documents d ON d.id = v.document_id JOIN platform.tenants t ON t.id = d.tenant_id ORDER BY t.slug, d.title, v.version_no"

reindex: ## enqueue document.reindex jobs for active versions with a stale index generation, then drain
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.documents.reindex
	$(MAKE) --no-print-directory ingest

rescan: ## upgrade back-fill: pseudonymise and risk-score every indexed version the M5 pipeline has not scanned, then drain
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.documents.reindex --rescan
	$(MAKE) --no-print-directory ingest

chat-smoke: ## live LLM smoke inside the api container (needs GEMINI_API_KEY / GROQ_API_KEY): ARGS="--order groq" or "--gemini-timeout 0.01"
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.agent.smoke $(ARGS)

chat-perf: ## NFR-PERF-01: 20 chat turns excluding LLM latency inside the api container (ARGS="--reranker BAAI/bge-reranker-v2-m3")
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.agent.perf $(ARGS)

retention-scan: ## run the retention scan for every tenant now (creates HR review items), then drain deletion jobs
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.retention.scan
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.worker --drain

security-check: ## score the EL/EN injection fixtures with the live heuristics + classifier inside the api container
	$(COMPOSE) --profile core exec -T api python -m leave_assistant.security.check

token: ## print an access token for EMAIL=<seed user e-mail> (dev realm, direct grant)
	@set -a && . ./deploy/.env && set +a && curl -fsS -d "grant_type=password&client_id=frontend-spa&username=$(EMAIL)&password=$${SEED_USER_PASSWORD}" \
	  "http://localhost:$${KEYCLOAK_PORT:-8081}/realms/leave-assistant/protocol/openid-connect/token" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'

smoke: ## liveness/readiness through the frontend proxy
	@curl -fsS http://localhost:$${FRONTEND_PORT:-8080}/api/v1/healthz && echo && curl -sS http://localhost:$${FRONTEND_PORT:-8080}/api/v1/readyz && echo

stats: ## memory/cpu per container (one sample)
	docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"

precommit: ## run pre-commit hooks on all files
	pre-commit run --all-files

clean: ## remove build artefacts (keeps volumes)
	rm -rf backend/.venv backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache frontend/node_modules frontend/dist
