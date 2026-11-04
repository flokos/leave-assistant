# ADR-0008 — LangGraph PostgreSQL checkpointer in its own schema and role; only pseudonymised state is persisted

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0003, ADR-0007, ADR-0017

## Context
Brief §6.1 selects the LangGraph PostgreSQL checkpointer with unique thread IDs, resumable workflows, restarts and
history, and requires graph state, conversation history and business records to stay logically distinct.

## Problem
The checkpointer stores opaque blobs keyed by `thread_id`; it cannot participate in RLS or know tenants, and it
would happily persist anything a node puts in state, including PII.

## Decision
- `AsyncPostgresSaver` (`langgraph-checkpoint-postgres` 3.2.x) with a dedicated pool and DB role `langgraph_rw`
  restricted to schema `langgraph`.
- Thread registry `app.conversations` (RLS) is checked for ownership before any run/resume; `thread_id` = UUIDv7.
- State content policy: pseudonymised text only; references instead of payloads; no tokens or mappings; a test
  serialises state and asserts the absence of vault values.
- Display history lives in `app.conversation_messages`; business records in their own tables; checkpoints are
  deleted by retention jobs without touching either.

## Alternatives considered
- In-memory saver: loses interrupts on restart (violates the brief).
- Storing checkpoints in the app schema with a `tenant_id` column: the library controls the schema; forking it is
  not worth it.
- SQLite saver: a second database engine for no benefit.

## Advantages
Durable interrupts and history; isolation of the opaque store from business tables; privilege separation.

## Disadvantages
Checkpoints are not tenant-filterable at the SQL level; isolation relies on the app-side ownership check (tested).

## Security implications
A compromise of `langgraph_rw` exposes only pseudonymised state; a compromise of `app_rls` cannot read checkpoints.

## Operational complexity
One extra role; `setup()` run during migrations; a cleanup job.

## Consequences
Run history exposed to users is a redacted projection stored in `agent_runs`, not raw checkpoints.
