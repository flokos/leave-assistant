# ADR-0007 — LangGraph: single orchestrator, three subgraphs, trusted runtime context

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0006, ADR-0008, ADR-0009, ADR-0010

## Context
Brief §6 selects LangGraph with one orchestrating agent and modular subgraphs (Knowledge, Business Operations,
Approval) and lists the capabilities to demonstrate. §4.6 requires that the LLM cannot choose or override its
tenant identity.

## Problem
Design the graph so that the model coordinates but cannot authorise, state stays small and checkpointable, and the
three subgraphs have crisp responsibilities.

## Decision
`StateGraph(AgentState, context_schema=RunContext)`; `RunContext` (tenant, user, roles, budgets, provider policy)
is injected by the API from the verified JWT and is never part of state. Orchestrator nodes: `ingress`
(deterministic), `classify` (pre-router + one structured LLM call), subgraph dispatch, `compose` (deterministic).
Knowledge = retrieve → assess → generate → verify citations. Business = plan (tools bound per role) ↔ tool gateway,
ending in an answer or a proposal. Approval = render → `interrupt` → validate → execute → summarise. Re-identification
happens outside the graph.

## Alternatives considered
- Free-form ReAct agent with all tools: fewer nodes, but no demonstrable routing, weaker control of phases.
- Multiple autonomous agents: explicitly rejected by the brief.
- Plain chat loop: explicitly rejected by the brief.

## Advantages
Conditional routing, typed state, subgraphs, interrupts, checkpointing, retries and error recovery are all first-class
and testable per node; trusted context is structurally separated from model-writable state.

## Disadvantages
More nodes than a minimal agent; LangGraph API evolution (1.x) must be tracked (pinned).

## Security implications
Authorization never reads state; the model only sees pseudonymised, permission-filtered inputs; execution tools are
not exposed to the model.

## Operational complexity
Checkpointer schema and role (ADR-0008); prompt files versioned.

## Consequences
Evaluation can target nodes (router accuracy, evidence assessment) independently of end-to-end quality.

**Implementation note M7 (2026-11-06, worklog M7):** the three subgraphs are compiled with explicit `output_schema`s and `messages` uses an id-based reducer — M6's Knowledge subgraph returned its full state and duplicated the history on every turn. The Approval subgraph renders the card, pauses with `interrupt()` and continues with `Command(resume=…)` on the owner's next request; a new message on a paused thread starts a fresh run (verified in LangGraph 1.2.19), so the open proposal is superseded.
