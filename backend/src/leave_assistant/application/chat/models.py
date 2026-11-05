"""Conversation records (doc 07 §4.6). Text is stored pseudonymised; `provenance` maps each placeholder to the source
that produced it so re-identification at read time can apply the doc 08 §7.4 matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class ConversationStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM_NOTE = "system_note"


@dataclass(slots=True)
class Conversation:
    id: UUID  # = LangGraph thread_id
    tenant_id: UUID
    employee_id: UUID
    user_id: str
    title: str
    status: ConversationStatus
    locale: str
    created_at: datetime
    last_activity_at: datetime
    run_lock_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str  # pseudonymised
    created_at: datetime
    citations: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)  # placeholder → {kind, id}
    run_id: UUID | None = None
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    turn: int
    intent: str | None
    language: str | None
    route: str | None
    provider_used: str | None
    model_used: str | None
    failovers: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    outcome: str
    created_at: datetime
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    langfuse_trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderEventRecord:
    id: UUID
    tenant_id: UUID
    provider: str
    model: str
    event: str
    reason: str
    occurred_at: datetime
    latency_ms: int | None = None
    run_id: UUID | None = None
