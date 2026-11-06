"""Typed state (doc 04 §2): pseudonymised text and small references only — it is checkpointed on every step."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

from leave_assistant.domain.agent.types import Intent


class AgentState(TypedDict, total=False):
    messages: Annotated[
        list[dict[str, str]], operator.add
    ]  # {"role": "user"|"assistant", "content": pseudonymised}
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
