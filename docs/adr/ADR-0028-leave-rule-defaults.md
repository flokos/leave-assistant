# ADR-0028 — Leave, sick-leave, routing, visibility and retention defaults decided from the brief's principles

**Status:** Accepted (2026-11-04) · **Related:** ADR-0005, ADR-0006, ADR-0027, ADR-0029

## Context
The brief fixes the leave domain (types, ledger, configurable approval rules, supporting documents, retention) but
leaves several business semantics open: entitlement granting, carry-over, half days, negative balances, when sick
leave needs HR review, who approves a manager's own request, fallback approvers, what managers may see, retention
defaults and chat retention. The owner asked for these to be decided in the spirit of the brief rather than by
further questions.

## Problem
Choose defaults that are deterministic, configurable per company, privacy-preserving and simple, without expanding
scope — and make them easy to change later.

## Decision
1. **Entitlement (Q-01):** up-front grant on 1 January (`grant_mode = upfront`), pro-rated on hire by remaining full
   months with 0.5-day rounding; carry-over cap 5 days expiring 31 March (`CARRYOVER`/`EXPIRY` ledger entries); whole
   days only (half days deferred, schema-ready); no negative balances — only an audited HR `ADJUSTMENT` with a reason.
2. **Sick leave (Q-02):** never a manager approval step. ≤ 3 working days without a document and within the annual
   cap → auto-acknowledged (APPROVED at submission, ledger deduction, manager and HR notified). Longer, documented, or
   cap-exceeding sick leave → HR review step. Retroactive reporting up to 30 days. Thresholds are tenant data.
3. **Routing (Q-03):** a department manager's own request re-designates the manager step to the `manager_override`
   if set, else to HR; HR Administrators may decide any pending manager step as an audited fallback (`allow_hr_fallback`,
   default on); nobody decides a step on their own request; a user holding both roles cannot decide both steps.
4. **Visibility (Q-09):** managers see leave type and reason for annual/unpaid requests routed to them; for sick
   leave only the type; never reasons, documents or HR review details. Availability views, company-administrator views
   and calendar events show absence only.
5. **Retention (Q-08):** supporting documents 365 days after the leave end date (`notify_only` → HR decides);
   superseded/retired company documents 730 days (`notify_only`); conversations, messages, checkpoints and agent runs
   90 days, automatic deletion; leave records, ledger and audit log never deleted by the application. Seed values are
   labelled "placeholder, not legal advice".
6. **Chat access (Q-14):** owners see their own full history; HR and Company Administrators see redacted run history
   only (no message text), audited; Platform Administrators see nothing.
7. **Organisation (Q-11):** one department per employee, one manager per department, per-employee override.

## Alternatives considered
- Monthly accrual as default: more entries and edge cases (hire/termination mid-month) for no demonstrated need;
  supported later by periodic `GRANT` entries without migration.
- Manager approval for sick leave: conflicts with medical confidentiality and the brief's "managers cannot access
  confidential HR information".
- Automatic deletion of supporting documents at retention date: violates the brief's requirement for HR review.
- Showing managers only "absence" for sick leave: hides information needed for planning and payroll-adjacent
  processes; the type alone carries no medical detail.

## Advantages
Deterministic, data-driven rules; privacy by need-to-know; no stuck workflows; defaults reversible by configuration.

## Disadvantages
Seed defaults are opinions, not legal or HR policy; each tenant must review them (the UI labels them as placeholders).

## Security implications
Sick-leave details stay within HR; fallback decisions are explicit and audited; segregation of duties is enforced.

## Operational complexity
None beyond configuration fields (`grant_mode`, `allow_hr_fallback`, retention policies).

## Consequences
Doc 01 §6, doc 07, doc 08 §5.2 and the evaluation dataset (S31, S32) encode these rules; changing a default is a
configuration or seed change plus a test update, not an ADR change — unless the principle behind it changes.
