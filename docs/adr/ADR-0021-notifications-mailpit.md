# ADR-0021 — Notifications: PostgreSQL records, n8n dispatch, Mailpit instead of MailHog

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0019

## Context
Brief §10.3 selects in-app notifications plus simulated e-mail, with PostgreSQL records, n8n workflows and MailHog
for local capture; no real e-mail provider.

## Problem
MailHog has been unmaintained for years; the project needs a maintained SMTP sink with an API usable by tests.

## Decision
Notifications are rows (`notifications`) created by application services in the business transaction, rendered per
recipient locale by the backend (`/internal/notifications/{id}/render`), and dispatched by n8n (W4) over SMTP to
**Mailpit** (`axllent/mailpit`, MIT, SMTP 1025 / UI+API 8025). In-app delivery reads the table directly (polling or
SSE). Delivery status is written back by n8n.

## Alternatives considered
- MailHog as specified: unmaintained; same ports as Mailpit, so the switch is a one-line change.
- Backend sending SMTP directly: works, but the brief assigns notification workflows to n8n.

## Advantages
Maintained tool; REST API for E2E assertions; identical developer experience.

## Disadvantages
Deviation from the named tool (documented here and in doc 14).

## Security implications
Rendered e-mails contain real names for authorised recipients only; n8n does not retain successful executions;
Mailpit is bound to localhost.

## Operational complexity
One small container.

## Consequences
Notification templates are i18n keys with parameters; the renderer decides what each recipient may see (e.g., no
leave type in calendar-style notices).
