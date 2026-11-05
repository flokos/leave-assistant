"""Notification records and outbox events (doc 07 §4.7). Rendering and dispatch arrive in M8."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class EmailStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class Notification:
    id: UUID
    tenant_id: UUID
    recipient_employee_id: UUID
    type: str
    params: dict[str, Any]
    channels: tuple[str, ...]
    created_at: datetime
    read_at: datetime | None = None
    email_status: EmailStatus = EmailStatus.PENDING
    email_attempts: int = 0
    dedupe_key: str | None = None


@dataclass(frozen=True, slots=True)
class IntegrationEvent:
    id: UUID
    tenant_id: UUID
    type: str
    payload: dict[str, Any]
    created_at: datetime
    delivered_at: datetime | None = None
    attempts: int = 0
    dead: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
