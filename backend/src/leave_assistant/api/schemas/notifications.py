from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.notifications.models import Notification


class NotificationOut(ApiModel):
    id: UUID
    type: str
    params: dict[str, Any]
    channels: list[str]
    created_at: datetime
    read_at: datetime | None
    email_status: str

    @classmethod
    def from_domain(cls, n: Notification) -> NotificationOut:
        return cls(
            id=n.id,
            type=n.type,
            params=n.params,
            channels=list(n.channels),
            created_at=n.created_at,
            read_at=n.read_at,
            email_status=n.email_status.value,
        )


class ReadAllOut(ApiModel):
    marked: int
