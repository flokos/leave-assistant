"""Typed state (doc 04 §2): pseudonymised text and small references only — it is checkpointed on every step."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

from leave_assistant.domain.agent.types import Intent


def merge_messages(left: list[dict[str, str]], right: list[dict[str, str]]) -> list[dict[str, str]]:
    """Append-only history that ignores messages it already holds (by `id`; equality for M6 messages without one).
    A subgraph that hands back the whole list therefore cannot duplicate the history (M6 bug, worklog M7)."""
    merged = list(left or [])
    seen = {m["id"] for m in merged if m.get("id")}
    for message in right or []:
        mid = message.get("id")
        if (mid and mid in seen) or (not mid and message in merged):
            continue
        merged.append(message)
        if mid:
            seen.add(mid)
    return merged


class AgentState(TypedDict, total=False):
    messages: Annotated[
        list[dict[str, str]], merge_messages
    ]  # {"id", "role": "user"|"assistant", "content": pseudonymised}
    summary: str  # rolling summary of turns older than the history window (pseudonymised)
    summarised: int  # how many messages the summary covers
    turn: int
    language: str
    input_risk: str  # low | medium | high
    intent: str
    confidence: float
    route: str
    query: str
    context: str  # <document> blocks of this turn (LLM-safe)
    refs: list[dict[str, Any]]  # citation map n → chunk/document/version
    sources: list[str]  # version ids behind the context
    evidence_ok: bool
    draft: dict[str, Any]
    feedback: str
    attempts: int
    answer: dict[str, Any]  # serialised AssistantAnswer
    # M7 — business operations (pseudonymised; tool results reference people by placeholder only)
    tool_steps: list[dict[str, Any]]  # this turn: {n, call_id, name, status, result}
    pending_calls: list[dict[str, Any]]  # tool calls of the last plan step, not yet executed
    placeholders: dict[str, dict[str, Any]]  # provenance of tool-result placeholders (re-identification)
    iterations: int
    proposal_id: str  # reference only — the proposal itself is a database row (doc 04 §2 invariant 3)
    proposal_kind: str
    proposal_round: int
    decision: dict[str, Any]  # the resume payload
    execution: dict[str, Any]  # {status, outcome, replayed}


class KnowledgeOutput(TypedDict, total=False):
    """What the Knowledge subgraph hands back. A subgraph compiled on the full state would return its copy of
    `messages` too, and the `operator.add` reducer would append the whole history again (M6 bug, worklog M7)."""

    query: str
    context: str
    refs: list[dict[str, Any]]
    sources: list[str]
    evidence_ok: bool
    draft: dict[str, Any]
    feedback: str
    attempts: int
    answer: dict[str, Any]


class BusinessOutput(TypedDict, total=False):
    tool_steps: list[dict[str, Any]]
    pending_calls: list[dict[str, Any]]
    placeholders: dict[str, dict[str, Any]]
    iterations: int
    proposal_id: str
    proposal_kind: str
    refs: list[dict[str, Any]]
    sources: list[str]
    draft: dict[str, Any]
    feedback: str
    attempts: int
    answer: dict[str, Any]


class ApprovalOutput(TypedDict, total=False):
    messages: Annotated[list[dict[str, str]], merge_messages]
    answer: dict[str, Any]
    proposal_id: str
    proposal_kind: str
    proposal_round: int
    decision: dict[str, Any]
    execution: dict[str, Any]
    tool_steps: list[dict[str, Any]]
    placeholders: dict[str, dict[str, Any]]


class IntentOutput(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)


class QueryOutput(BaseModel):
    query: str = Field(max_length=500)


class AnswerOutput(BaseModel):
    answer: str = Field(max_length=2500)
    citations: list[int] = Field(default_factory=list, max_length=10)
    confidence: Literal["high", "medium", "low"] = "medium"


class SummaryOutput(BaseModel):
    summary: str = Field(max_length=1500)
