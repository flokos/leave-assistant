"""Audit log entry (doc 07 §4.8, doc 08 §10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class ActorKind(StrEnum):
    USER = "user"
    AUTOMATION = "automation"
    SYSTEM = "system"
    AGENT = "agent"


class Outcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: UUID
    tenant_id: UUID | None
    occurred_at: datetime
    actor_kind: ActorKind
    actor_id: str
    actor_roles: tuple[str, ...]
    action: str
    resource_type: str
    resource_id: str | None
    outcome: Outcome
    request_id: str | None
    details: dict[str, Any] = field(default_factory=dict)
    thread_id: UUID | None = None
