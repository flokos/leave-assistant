# ADR-0005 — Ledger-based leave balances

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0006, ADR-0009

## Context
Brief §5.4 selects a ledger approach: no independently mutable balance, entries for grants, deductions,
adjustments, reversals and other authorised changes; transactional and auditable.

## Problem
Represent balances so that every change is explainable, reversible and idempotent, including modifications and
cancellations of approved leave, carry-over and expiry.

## Decision
`leave_ledger_entries` is INSERT-only (no UPDATE/DELETE grants to the runtime role) with signed `quantity_days`,
`entry_type ∈ {GRANT, DEDUCTION, REVERSAL, ADJUSTMENT, CARRYOVER, EXPIRY}`, `period_year`, a reference to the
request where applicable, and a unique `idempotency_key`. Balance is a SQL view (`SUM`); available = balance −
pending (computed from requests). Postings happen in the same transaction as the state transition that causes
them, under `SELECT … FOR UPDATE` on the employee's balance scope.

## Alternatives considered
- Mutable `balance` column with history table: simpler reads, but the column can drift from history.
- Event sourcing for the whole leave aggregate: more general than needed; the ledger is the event source for the
  one quantity that matters.

## Advantages
Auditability by construction; reversals are entries, not edits; idempotent postings; easy "balance as of" queries.

## Disadvantages
Reads aggregate (trivial at this scale; a cache can be added later without changing semantics).

## Security implications
Only services post entries; HR adjustments require a reason and are audited; the LLM never computes balances.

## Operational complexity
Year-start grants and expiries run as scheduled jobs; idempotency keys make re-runs safe.

## Consequences
Half days and future types need no schema change (`numeric(6,2)`).
