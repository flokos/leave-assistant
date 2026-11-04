# ADR-0006 — Deterministic approval engine and request state machine; cross-user approvals live in the database

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0005, ADR-0007, ADR-0009

## Context
Brief §5.5: approval rules are configurable per company and must be deterministic backend logic; §6 lists an
Approval subgraph with manager and HR approval, interrupts and human-in-the-loop.

## Problem
Approvals involve several users over days; graph interrupts are bound to one user's trusted context and one thread.
Rules must be data, not prompts.

## Decision
- `approval_rules` (per tenant: leave type, working-day range, document requirement, ordered steps, auto-acknowledge)
  are evaluated by a domain engine that materialises `approval_steps`.
- `leave_requests` follow a table-driven state machine with optimistic locking; transitions post ledger entries,
  events, notifications and outbox rows in one transaction.
- The LangGraph Approval subgraph handles the **acting user's** confirmation interrupt and controlled execution
  (employee submit/modify/cancel; manager/HR approve/reject in *their own* threads). A graph never waits for a
  different user.

## Alternatives considered
- Long-lived paused graph per request spanning users: demonstrates interrupts dramatically but mixes security
  contexts on resume, keeps checkpoints alive for days, and complicates restarts/retention.
- Rules in prompts: explicitly forbidden by the brief.

## Advantages
Business workflow is durable and queryable; graph runs are short and single-user; the same services serve UI and
chat; segregation of duties is enforceable.

## Disadvantages
Two mechanisms (DB workflow + graph interrupt) must be explained — this ADR and doc 04 §6.1 do so.

## Security implications
Approvers are validated against the step's designated role/department and the requester (no self-approval);
all decisions are audited with `via = ui|agent`.

## Operational complexity
None beyond the schema; a simulation endpoint lets admins test rules.

## Consequences
"Persistent execution" and "resume after restart" are demonstrated by the confirmation interrupt and the job
queue, not by cross-user graph pauses.
