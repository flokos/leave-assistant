# Leave Management AI Assistant — developer entry points (the contract; CI mirrors these targets).
SHELL := /bin/bash
COMPOSE := docker compose -f deploy/compose.yaml
COMPOSE_DEV := $(COMPOSE) -f deploy/compose.dev.yaml
UV := uv
NPM := npm

.PHONY: help setup env lint test-unit build up up-staged up-dev down ps logs smoke stats precommit clean

help: ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

env: ## create deploy/.env from the example if missing
	@test -f deploy/.env || (cp deploy/.env.example deploy/.env && echo "created deploy/.env — review the values")

setup: env ## install backend (uv) and frontend (npm) dependencies
	cd backend && $(UV) sync --all-groups
	cd frontend && $(NPM) ci

lint: ## ruff, ruff format, mypy, import-linter, eslint, tsc
	cd backend && $(UV) run --no-sync ruff check . && $(UV) run --no-sync ruff format --check . && $(UV) run --no-sync mypy && $(UV) run --no-sync lint-imports
	cd frontend && $(NPM) run lint && $(NPM) run typecheck

test-unit: ## backend pytest (with coverage) and frontend vitest
	cd backend && $(UV) run --no-sync pytest
	cd frontend && $(NPM) run test

build: env ## build the api and frontend images
	$(COMPOSE) --profile core build

up: env ## start the core profile (needs ~4 GB free RAM)
	$(COMPOSE) --profile core up -d --wait

up-staged: env ## low-memory hosts: infra first, worker embedded in the api process (doc 02 §10)
	$(COMPOSE) --profile core up -d --wait postgres seaweedfs mailpit
	$(COMPOSE) --profile core up -d --wait keycloak
	WORKER_MODE=embedded $(COMPOSE) --profile core up -d --wait --force-recreate api frontend   # worker container not started; loop runs inside api

up-dev: env ## core profile with hot reload and api published on :8000
	$(COMPOSE_DEV) --profile core up -d --wait

down: ## stop everything (volumes are kept; add -v manually to wipe data)
	$(COMPOSE) --profile core down

ps: ## service status
	$(COMPOSE) --profile core ps

logs: ## follow logs (SERVICE=api to filter)
	$(COMPOSE) --profile core logs -f $(SERVICE)

smoke: ## liveness/readiness through the frontend proxy
	@curl -fsS http://localhost:$${FRONTEND_PORT:-8080}/api/v1/healthz && echo && curl -sS http://localhost:$${FRONTEND_PORT:-8080}/api/v1/readyz && echo

stats: ## memory/cpu per container (one sample)
	docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"

precommit: ## run pre-commit hooks on all files
	pre-commit run --all-files

clean: ## remove build artefacts (keeps volumes)
	rm -rf backend/.venv backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache frontend/node_modules frontend/dist
