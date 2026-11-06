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

**Implementation note M7 (2026-11-06, worklog M7):** implemented as decided (`application/assistant/gateway.py`, `proposals.py`). Native function calling binds the role's tools per intent group; the loop's history is rendered as text so a failover never replays provider-specific tool messages. Exactly-once execution: an atomic claim on the proposal row, the same service call as REST under `acting_via_agent` (request events carry the proposal id), a stored outcome for replays, recovery from the request event after a crash, and a partial unique index on the request idempotency key. The security suite measures an unauthorized-action rate of 0 over 17 hostile tool calls plus text attacks, replays and segregation of duties.
