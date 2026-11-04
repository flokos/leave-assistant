# ADR-0019 — n8n for automation only, consuming a transactional outbox; PostgreSQL-backed job queue for heavy work

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0001, ADR-0021

## Context
Brief §8 selects self-hosted n8n CE for ingestion orchestration, update workflows, re-indexing triggers, lifecycle
automation, scheduled checks and notifications, while Python keeps parsing, chunking, embedding, security and
business logic; n8n must not become an agent orchestrator; licence terms must be respected.

## Problem
Give n8n real responsibilities without letting it hold logic or data it should not, and without making the backend
depend on n8n being up.

## Decision
- Heavy work runs in the backend worker via a `jobs` table (`FOR UPDATE SKIP LOCKED`, retries, dead-letter).
- Side effects are recorded in `integration_events` (outbox) within business transactions.
- n8n polls the outbox through `/internal/*` (client-credentials, `automation` role, internal network only), routes
  events to workflows (ingestion lifecycle, re-index checks, retention scans, notification dispatch to Mailpit,
  calendar maintenance, housekeeping, health digest), and acknowledges them. Workflows are JSON in the repository,
  imported at start-up. n8n never accesses the database or document content and does not retain successful execution data.
- Licence: Sustainable Use License 1.0 permits internal business use; no `.ee.` features.

## Alternatives considered
- n8n executing Python/HTTP steps for parsing or embeddings: violates the responsibility split.
- Webhooks from backend to n8n: requires inbound auth and retries on the backend side; polling is simpler and robust.
- Celery/Redis instead of the jobs table: more infrastructure for no local benefit.

## Advantages
Clear split; backend fully functional without n8n; automation visible and editable in n8n's UI; reliable delivery.

## Disadvantages
Polling latency (≤ 20 s) for e-mails and reminders; two schedulers conceptually (n8n cron + worker jobs) — acceptable
and documented.

## Security implications
The automation principal cannot read tenant content; rendered e-mails are the only re-identified data n8n handles,
and are not retained.

## Operational complexity
One container plus init import; Postgres database for n8n.

## Consequences
Every n8n workflow maps to documented internal endpoints with their own tests.
