# ADR-0020 — Google Calendar: outbox-based synchronisation, deterministic event IDs, reconciliation, mock adapter

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0019

## Context
Brief §10.1–§10.2: shared company and optional personal calendars via OAuth2; events expose only name, dates and
availability; automatic sync after final approval with retries, idempotency, history, failure tracking and manual
reconciliation; real and mock adapters.

## Problem
External side effects must be reliable and idempotent despite provider errors, expired OAuth grants (7-day refresh
tokens in Testing status) and application restarts, and must never leak leave details.

## Decision
`CalendarProvider` port with Google and mock adapters. Sync jobs are written to `calendar_sync_jobs` in the approval
transaction; the worker executes them with classified errors (transient → backoff; `AUTH_EXPIRED` → parked until
re-authorisation; permanent → dead) and records `calendar_events`. Event IDs are derived deterministically from the
request and connection (`events.insert` with client id; 409 → verify/patch), updates and deletes use stored external
IDs, and `extendedProperties.private` marks ownership. Daily reconciliation compares expected and actual events and
proposes actions in the admin UI. Backend-driven OAuth with signed state, `prompt=consent`, encrypted refresh tokens
and least-privilege granular scopes (verified at M8).

## Alternatives considered
- Synchronous API calls during approval: couples the business transaction to an external service.
- n8n performing the Google calls: moves credentials and retry logic out of the backend's security boundary.
- Two-way sync (reading Google changes): out of scope and a privacy risk.

## Advantages
Exactly-once effect per request/connection; restart-safe; visible failures; testable without credentials.

## Disadvantages
Reconciliation needs `events.list` with property filters per connection (cheap at this scale).

## Security implications
Minimal event content; tokens encrypted; scopes limited; company calendar sharing is the admin's responsibility
(documented).

## Operational complexity
Google Cloud project + OAuth client; re-authorisation every 7 days while in Testing status.

## Consequences
The mock adapter is the default in development and CI; the Google adapter is exercised by a manual checklist.
