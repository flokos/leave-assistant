from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.audit.entry import AuditEntry


class AuditEntryOut(ApiModel):
    id: UUID
    tenant_id: UUID | None
    occurred_at: datetime
    actor_kind: str
    actor_id: str
    actor_roles: list[str]
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    request_id: str | None
    details: dict[str, Any]

    @classmethod
    def from_domain(cls, entry: AuditEntry) -> AuditEntryOut:
        return cls(
            id=entry.id,
            tenant_id=entry.tenant_id,
            occurred_at=entry.occurred_at,
            actor_kind=entry.actor_kind.value,
            actor_id=entry.actor_id,
            actor_roles=list(entry.actor_roles),
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            outcome=entry.outcome.value,
            request_id=entry.request_id,
            details=entry.details,
        )
