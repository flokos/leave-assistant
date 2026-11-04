# ADR-0002 — Layered backend: API → application → domain, with ports and adapters

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0001, ADR-0009

## Context
Brief §4.3 requires clear boundaries between API, application services, domain logic and infrastructure, and
§15 wants every component to have a clearly defined responsibility. Several infrastructure choices must be
swappable (vector backend, calendar provider, PII engine, LLM provider, storage).

## Problem
Prevent framework and vendor details (SQLAlchemy, boto3, LangGraph, Google APIs) from leaking into business rules,
and make the deterministic core testable without containers.

## Decision
Four layers with enforced import rules (`import-linter`): `domain` (pure Python rules, state machines, value
objects), `application` (use cases, authorization policy, ports as `Protocol`s), `infrastructure` (adapters:
DB, storage, identity, LLM, ML, parsing, vector, calendar, observability), `api` (FastAPI routers/schemas).
The `agent` package sits beside `application`, uses services through the tool gateway, and is never imported by
`application`.

## Alternatives considered
- "Fat routers" with ORM models as the domain: fastest start, but authorization and rules end up duplicated in
  endpoints and tools.
- Full DDD with aggregates/repositories everywhere: more ceremony than this domain needs.

## Advantages
Unit tests for rules run in milliseconds; adapters can be replaced (Chroma vs pgvector, mock vs Google) by
configuration; the same `authorize()` protects REST and tools.

## Disadvantages
More files and some mapping code (DTOs ↔ ORM models).

## Security implications
Authorization lives in exactly one place (`application/authz`); infrastructure cannot bypass it because it does not
own business flows.

## Operational complexity
None at runtime; a CI contract check.

## Consequences
Any new capability starts with a port and a domain rule, then an adapter; this is the gate against scope creep
and against "LLM-owned" logic.
