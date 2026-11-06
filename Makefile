# Leave Management AI Assistant — developer entry points (the contract; CI mirrors these targets).
SHELL := /bin/bash
COMPOSE := docker compose -f deploy/compose.yaml
COMPOSE_DEV := $(COMPOSE) -f deploy/compose.dev.yaml
UV := uv
NPM := npm

.PHONY: help setup env lint test-unit test-integration test-e2e test-ocr contract build up up-staged up-dev down ps logs smoke stats migrate seed bootstrap rollover models ingest jobs reindex rescan chat-smoke chat-perf retention-scan security-check token precommit clean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

env: ## create deploy/.env from the example if missing; generate empty keys (M5 encryption, M8 n8n)
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

up-staged: env ## low-memory hosts: infra first, worker embedded in the api process (doc 02 §10); api limit 3.5 GB for the ingestion child (M9: ONNX reranker)
	$(COMPOSE) --profile core up -d --wait postgres seaweedfs mailpit
	$(COMPOSE) --profile core up -d --wait keycloak
	WORKER_MODE=embedded API_MEMORY_LIMIT=3584M $(COMPOSE) --profile core up -d --wait --force-recreate api frontend   # worker container not started; loop runs inside api

up-dev: env ## core profile with hot reload and api published on :8000
	$(COMPOSE_DEV) --profile core up -d --wait

up-automation: env ## add the automation profile: n8n-init imports and publishes W0–W7, then n8n on 127.0.0.1:5678
	$(COMPOSE) --profile core --profile automation up -d

n8n-export: ## export the workflows edited in the n8n UI back into automation/n8n/workflows (then review and commit)
	$(COMPOSE) --profile automation exec -T n8n n8n export:workflow --all --separate --pretty --output=/tmp/export
	$(COMPOSE) --profile automation cp n8n:/tmp/export/. automation/n8n/workflows/

up-observability: env ## add Langfuse (observability profile: web, worker, ClickHouse, Valkey; UI http://localhost:3000)
	$(COMPOSE) --profile core --profile observability up -d --wait langfuse-bucket clickhouse valkey langfuse-worker langfuse-web

eval-session: env ## evaluation session on the 7 GB machine (Q-05, doc 11 §8): stop frontend/n8n/worker/api/keycloak, start Chroma + Langfuse
	$(COMPOSE) --profile core --profile automation stop frontend n8n worker api keycloak
	$(COMPOSE) --profile core --profile eval --profile observability up -d --wait postgres seaweedfs chroma langfuse-bucket clickhouse valkey langfuse-worker langfuse-web
	@docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | grep leave-assistant

eval-session-end: ## stop Chroma and Langfuse, back to the staged core stack
	$(COMPOSE) --profile eval --profile observability --profile core stop chroma langfuse-web langfuse-worker clickhouse valkey
	$(MAKE) up-staged

EVAL_RUN = set -a && . ./deploy/.env && set +a && cd backend && $(UV) run --no-sync python -m

EVAL_RUNTIME_VARIANTS ?= --variant B:torch --variant B:onnx --variant B:onnx-int8  # A: see retrieval-comparison §3

eval-offline: ## retrieval grid (pgvector/Chroma × rerankers × hybrid/vector), reranker runtimes, τ, security figures → docs/evaluation/results
	$(EVAL_RUN) leave_assistant.evaluation.retrieval grid
	$(EVAL_RUN) leave_assistant.evaluation.retrieval runtimes $(EVAL_RUNTIME_VARIANTS)
	$(EVAL_RUN) leave_assistant.evaluation.security

EVAL_CONFIGS ?= --config pgvector+B+hybrid+onnx:0.22 --config pgvector+A+vector:0.135
EVAL_PROVIDERS ?= gemini,mistral
EVAL_ANSWER_PROVIDERS ?= mistral  # one provider for every configuration's answers: comparable RAGAS

eval-live: ## live answers for RAGAS (EVAL_CONFIGS="--config backend+A|B+mode:τ …") + live agent scenarios (EVAL_PROVIDERS); budget EVAL_MAX_CALLS
	$(EVAL_RUN) leave_assistant.evaluation.answers --providers $(EVAL_ANSWER_PROVIDERS) $(EVAL_CONFIGS)
	$(EVAL_RUN) leave_assistant.evaluation.agent_live --providers $(EVAL_PROVIDERS)

eval-trace: ## one chat turn through the running api (LANGFUSE_ENABLED=true make up-staged) read back from Langfuse; fails on any leak
	$(EVAL_RUN) leave_assistant.evaluation.trace_check

eval-ragas: ## RAGAS scoring (judge: Mistral) and the Langfuse dataset + experiments (separate evaluation/ project)
	set -a && . ./deploy/.env && set +a && cd evaluation && $(UV) run python -m leave_eval.ragas_run $(ARGS)
	set -a && . ./deploy/.env && set +a && cd evaluation && $(UV) run python -m leave_eval.langfuse_sync $(ARGS)

eval-report: ## performance figures (stage latency, ingestion times) from the results and the job history
	$(EVAL_RUN) leave_assistant.evaluation.perf $(ARGS)

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

chat-perf: ## NFR-PERF-01: 20 chat turns excluding LLM latency, one-off api container (same image, env, limit); stop the api first on 7 GB hosts
	$(COMPOSE) --profile core run --rm --no-deps -T api python -m leave_assistant.agent.perf $(ARGS)   # M9: `exec` into the running api loaded every model twice under one memory limit

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
