from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

JOB_DOCUMENT_PROCESS = "document.process"
JOB_DOCUMENT_REINDEX = "document.reindex"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # attempts exhausted: dead letter, visible to administrators, manual retry
    DEAD = "dead"  # reserved: abandoned by an operator


@dataclass(slots=True)
class Job:
    id: UUID
    type: str
    payload: dict[str, Any]
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    tenant_id: UUID | None = None
    attempts: int = 0
    max_attempts: int = 3
    run_after: datetime | None = None
    locked_by: str | None = None
    locked_at: datetime | None = None
    heartbeat_at: datetime | None = None
    last_error: str | None = None
    dedupe_key: str | None = None
    finished_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # free-form bookkeeping (e.g. timings)
