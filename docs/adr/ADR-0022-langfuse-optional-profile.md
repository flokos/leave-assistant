# ADR-0022 — Observability: self-hosted Langfuse v4 as an optional Compose profile, with Valkey and SeaweedFS blobs

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0017, ADR-0023, ADR-0025

## Context
Brief §12.1 selects self-hosted Langfuse for LLM/agent/tool traces, prompts, latency, tokens and experiments, with
no sensitive personal information or sick-leave content stored there. Verified facts (2026-11-04): Langfuse v4
requires PostgreSQL (≥15), ClickHouse, Redis/Valkey and S3-compatible blob storage; its reference compose pins
`clickhouse-server:26.3.x`. The development machine has 7.1 GB RAM.

## Problem
Keep Langfuse self-hosted and used for all evaluation while not making the application depend on ~3–5 GB of
additional infrastructure.

## Decision
Langfuse web + worker + ClickHouse + Valkey run in the `observability` Compose profile; PostgreSQL is the shared
instance (database `langfuse`); SeaweedFS provides the S3 buckets. The backend's tracing is a feature flag
(`LANGFUSE_ENABLED`) using the Langfuse Python SDK 5.x with a `mask` callback; all inputs are already pseudonymised.
Valkey (BSD-3) replaces Redis (licence); the ClickHouse tag follows Langfuse's compose file.

## Alternatives considered
- Langfuse Cloud free tier: no local footprint, but traces leave the host and the brief selects self-hosting.
- OpenTelemetry → local Jaeger/Tempo: lighter, but loses Langfuse's LLM-specific views, datasets and experiments.
- Langfuse v2 (lighter, Postgres-only): end-of-life.

## Advantages
Full Langfuse capability when needed; core profile stays small; licence-clean components.

## Disadvantages
Evaluation sessions need a ≥16 GB host or a reduced ClickHouse configuration; traces are unavailable when the
profile is down (logs still carry correlation IDs).

## Security implications
Masking plus pseudonymisation; supporting documents never traced by construction; Langfuse bound to localhost with
its own credentials.

## Operational complexity
Four extra containers in the profile; ClickHouse disk growth is bounded by retention settings.

## Consequences
RAGAS runs use Langfuse datasets/experiments (ADR-0023); provider health digests do not depend on Langfuse.
