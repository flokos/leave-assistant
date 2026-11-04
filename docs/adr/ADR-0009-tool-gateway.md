# ADR-0009 — Tool gateway: propose → validate → confirm → execute, with idempotency

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0006, ADR-0007, ADR-0018

## Context
Brief §6.2: the LLM may propose tool calls; the backend decides; separate retrieving, preparing, confirming and
executing; validate identity, tenant, role, ownership, rules and approval state; idempotency for every
state-changing operation.

## Problem
Give the model useful tools without ever letting it execute a privileged operation, and make duplicate or replayed
actions harmless.

## Decision
All tools pass through one gateway: role allowlist → Pydantic argument validation (with bounded repair) →
`authorize()` (same policy as REST) → service call under RLS → pseudonymised, size-bounded result → trace record.
Tools are read-only or `prepare_*` (which create an `action_proposals` row with a content hash). Execution exists
only in the Approval subgraph after a human `Command(resume)` bound to the proposal id, which doubles as the
idempotency key; the service re-validates inside the transaction.

## Alternatives considered
- Exposing `submit/approve` tools guarded by prompt instructions: unacceptable (prompt is not a control).
- Per-tool ad-hoc checks: inconsistent; drift from REST authorization.

## Advantages
One enforcement point; identical semantics via UI and chat; replay-safe; auditable tool trace.

## Disadvantages
Two round-trips for actions (propose, confirm) — intended by the brief.

## Security implications
Argument manipulation (foreign IDs) is caught by ownership checks; unknown tools are denied and audited; medium-risk
inputs restrict the allowlist to read-only tools.

## Operational complexity
None.

## Consequences
Tool additions require a schema, a policy entry and tests, which keeps the catalogue small and reviewed.
