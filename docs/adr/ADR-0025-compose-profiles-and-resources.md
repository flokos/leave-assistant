# ADR-0025 — Docker Compose profiles and an explicit resource budget

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0001, ADR-0022

## Context
Brief §4.1: everything runs locally in Docker Compose with persistent volumes and a reproducible environment. The
measured development machine has 4 cores and 7.1 GB RAM; the full stack (Keycloak, Langfuse v4, n8n, Docling,
embedding and reranking models) exceeds that.

## Problem
Make the system runnable on the current machine while keeping all selected components available.

## Decision
Profiles: `core` (postgres, keycloak, seaweedfs, mailpit, api, worker, frontend), `automation` (n8n), `observability`
(langfuse web/worker, clickhouse, valkey), `eval` (chroma). Memory limits per container, tuned Keycloak heap and
PostgreSQL memory, `WORKER_MODE=embedded` option, lazy model loading, int8 reranker option. Documented budgets:
`core` 3.5–6 GB; full stack 7–11 GB → 16 GB host recommended. One PostgreSQL instance hosts four databases.

## Alternatives considered
- Everything always on: fails on the reference machine.
- Kubernetes (kind/k3s): unnecessary complexity for a single host.

## Advantages
Reproducible startup for the common case; the full stack remains one command away on a larger host.

## Disadvantages
Some features (e-mail, Langfuse views) require starting an extra profile; documented in operations.

## Security implications
Only `edge` network ports are published; internal services are unreachable from the browser.

## Operational complexity
Profiles and healthchecks in one compose file plus a dev override.

## Consequences
NFR-RES-01 is measured at M10 and the budget table corrected with real numbers.
