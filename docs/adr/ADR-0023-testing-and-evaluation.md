# ADR-0023 — Testing and evaluation: Pytest with fake providers for correctness; RAGAS + Langfuse for quality

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0010, ADR-0022

## Context
Brief §12.2: Pytest for unit/integration/authorization/tenancy/idempotency/workflow/security; RAGAS for retrieval
and answer quality; Langfuse for tracing and experiments; evaluation must distinguish deterministic correctness from
probabilistic quality; security tests must run without paid LLM calls.

## Problem
Keep the deterministic suites fast, hermetic and CI-blocking while measuring LLM quality honestly under free-tier
quotas.

## Decision
A `FakeProvider` with scripted responses and failure injection backs all correctness and security tests; real
PostgreSQL/SeaweedFS/Keycloak containers back integration tests; a 30-scenario dataset (doc 11 §6) is used both
deterministically (fake) and live (RAGAS metrics, retrieval metrics, agent/security metrics) through Langfuse
datasets and `run_experiment`, with a call budget guard. Results are published only from harness output with
configuration and date.

## Alternatives considered
- Live LLM calls in CI: non-deterministic, quota-bound, costly.
- Mocking the database: would not test RLS, FTS or pgvector, which are core to security and retrieval.
- DeepEval/other frameworks instead of RAGAS: RAGAS is the brief's selection and integrates with Langfuse.

## Advantages
Fast, hermetic CI; security suite always green by construction; quality measured comparably over time.

## Disadvantages
Fake responses must be maintained alongside prompts; judge-model noise in RAGAS.

## Security implications
Security regressions are CI failures; PII leak tests inspect outbound payloads, checkpoints, logs and traces.

## Operational complexity
Compose `test` profile; `make` targets; optional GitHub Actions mirror.

## Consequences
Milestone exit criteria reference named suites; evaluation documents carry confidence intervals and limitations.
