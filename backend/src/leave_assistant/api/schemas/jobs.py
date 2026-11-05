from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.jobs.models import Job


class JobOut(ApiModel):
    id: UUID
    type: str
    status: str
    tenant_id: UUID | None
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    run_after: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_domain(cls, j: Job) -> JobOut:
        return cls(
            id=j.id,
            type=j.type,
            status=j.status.value,
            tenant_id=j.tenant_id,
            payload=j.payload,
            attempts=j.attempts,
            max_attempts=j.max_attempts,
            run_after=j.run_after,
            last_error=j.last_error,
            created_at=j.created_at,
            updated_at=j.updated_at,
            finished_at=j.finished_at,
        )
