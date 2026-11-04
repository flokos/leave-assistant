# ADR-0003 — Shared PostgreSQL with Row-Level Security and transaction-scoped tenant context

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0004, ADR-0008, ADR-0011

## Context
Brief §4.6 selects a shared database with RLS, independent application authorization, defence in depth, and
explicitly warns about connection pooling and transaction-scoped context.

## Problem
Guarantee that one request can never read or write another tenant's rows — even when the application layer has a
bug — without giving up a single database and connection pool.

## Decision
- Every tenant table has `tenant_id`; `ENABLE` **and** `FORCE ROW LEVEL SECURITY`; policy
  `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid` for `USING` and `WITH CHECK`.
- Runtime role `app_rls` is `NOBYPASSRLS` and not the table owner; migrations run as `app_owner`.
- The API sets `SET LOCAL app.tenant_id/app.user_id/app.roles` in SQLAlchemy's `after_begin` hook from a request
  contextvar; the worker sets it from the job row. `SET LOCAL` ends with the transaction; pool reset is `rollback`.
- The application layer still checks tenant ownership on every loaded resource.
- A meta-test asserts every `tenant_id` table has a forced policy; a concurrency test proves no leakage on a
  tiny pool. The `NULLIF` form was validated empirically on PG 18.6 (after a `SET LOCAL` transaction ends, the
  setting reads `''`, which would make a bare `::uuid` cast error).

## Alternatives considered
- Schema-per-tenant: strong isolation but migrations × N, connection routing, and dynamic tenant creation becomes
  DDL at runtime.
- Database-per-tenant: strongest isolation; incompatible with one pool/one Keycloak-style simplicity locally.
- Application-only filtering: a single missed `WHERE` leaks data.

## Advantages
One schema, one migration path, dynamic tenants are rows; database-enforced isolation complements app checks.

## Disadvantages
RLS adds planner overhead (negligible here); correctness depends on discipline (hence meta-tests and the forced
policy).

## Security implications
Superuser/owner connections bypass RLS by nature — they are never used at runtime; `BYPASSRLS` is never granted;
platform admins have no tenant context and therefore see no tenant rows.

## Operational complexity
Roles and grants in init SQL; a helper in migrations to apply the standard policy.

## Consequences
pgvector and FTS queries inherit isolation automatically (ADR-0011); the LangGraph checkpointer, which cannot carry
tenant predicates, is isolated by a separate schema/role and by ownership checks in app tables (ADR-0008).
