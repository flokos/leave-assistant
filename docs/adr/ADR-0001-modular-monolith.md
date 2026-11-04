# ADR-0001 — Modular monolith with a separate worker process

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0002, ADR-0019, ADR-0025

## Context
The brief (§15) asks for simple solutions, no unnecessary microservices or distributed infrastructure, a local
Docker Compose deployment and clear component responsibilities. The system nevertheless has CPU-heavy
background work (parsing, OCR, embedding) that must not block the interactive API.

## Problem
How to structure the backend so that it stays simple to run and reason about locally, while isolating heavy
background work from request latency and keeping one codebase with one set of domain rules.

## Decision
One Python codebase and one container image, deployed as two processes: `api` (FastAPI) and `worker` (job loop
over a PostgreSQL `jobs` table). On low-memory hosts the worker loop can run inside the API process
(`WORKER_MODE=embedded`). No message broker; no separate services per domain.

## Alternatives considered
- Microservices (documents, leave, agent as separate services): clearer scaling story, but far more operational
  surface, duplicated auth/tenancy code, and no benefit at local scale.
- Celery/RQ with Redis: mature, but adds a broker and a second durability model next to PostgreSQL.
- Single process only: simplest, but Docling/OCR would starve the API on a 4-core machine.

## Advantages
Single deployable; shared domain code; transactional enqueue of jobs with business changes; minimal resource use.

## Disadvantages
No independent scaling of components (irrelevant locally); worker and API must be released together.

## Security implications
One authorization policy and one RLS context mechanism for both processes; the worker runs under the same
`NOBYPASSRLS` role and sets the job's tenant context explicitly.

## Operational complexity
Low: two processes from one image; health files for the worker; jobs visible in the admin UI.

## Consequences
The package layout in doc 03 §4.1 and the import-linter contracts keep the monolith modular; extracting a
service later would start from a package boundary, not from scratch.
