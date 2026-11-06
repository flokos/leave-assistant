"""Calendar API schemas (doc 09 §2.7). Tokens, DEKs and calendar ids of other people never leave the backend."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from leave_assistant.application.calendar.models import CalendarConnection, ReconciliationItem
from leave_assistant.application.calendar.reconcile import ReconciliationReport
from leave_assistant.application.calendar.sync import SyncJobView


class ConnectionOut(BaseModel):
    id: UUID
    kind: str
    provider: str
    status: str
    calendar: str | None  # "primary" or the app-created calendar's id
    scopes: list[str]
    last_success_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, c: CalendarConnection) -> ConnectionOut:
        return cls(
            id=c.id,
            kind=c.kind.value,
            provider=c.provider,
            status=c.status.value,
            calendar=c.external_calendar_id,
            scopes=list(c.scopes),
            last_success_at=c.last_success_at,
            last_error=c.last_error,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )


class ConnectionsOut(BaseModel):
    provider: str
    items: list[ConnectionOut]


class StartIn(BaseModel):
    kind: Literal["company_shared", "personal"]


class StartOut(BaseModel):
    authorization_url: str


class SyncJobOut(BaseModel):
    id: UUID
    request_id: UUID
    request_number: str
    connection_id: UUID
    connection_kind: str | None
    operation: str
    status: str
    attempts: int
    next_attempt_at: datetime | None
    error_class: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @classmethod
    def from_view(cls, v: SyncJobView) -> SyncJobOut:
        j = v.job
        return cls(
            id=j.id,
            request_id=j.request_id,
            request_number=v.request_number,
            connection_id=j.connection_id,
            connection_kind=v.connection_kind.value if v.connection_kind else None,
            operation=j.operation.value,
            status=j.status.value,
            attempts=j.attempts,
            next_attempt_at=j.next_attempt_at,
            error_class=j.error_class.value if j.error_class else None,
            last_error=j.last_error,
            created_at=j.created_at,
            updated_at=j.updated_at,
            finished_at=j.finished_at,
        )


class SyncJobsOut(BaseModel):
    items: list[SyncJobOut]


class ReconciliationItemOut(BaseModel):
    id: UUID
    connection_id: UUID
    finding: str
    status: str
    request_id: UUID | None
    external_event_id: str | None
    action: str | None
    details: dict[str, object]
    resolved_at: datetime | None

    @classmethod
    def from_domain(cls, i: ReconciliationItem) -> ReconciliationItemOut:
        return cls(
            id=i.id,
            connection_id=i.connection_id,
            finding=i.finding.value,
            status=i.status.value,
            request_id=i.request_id,
            external_event_id=i.external_event_id,
            action=i.action,
            details=dict(i.details),
            resolved_at=i.resolved_at,
        )


class ReconciliationOut(BaseModel):
    run_id: UUID | None
    run_at: datetime | None
    counts: dict[str, int]
    errors: dict[str, str]
    items: list[ReconciliationItemOut]

    @classmethod
    def from_report(cls, r: ReconciliationReport) -> ReconciliationOut:
        return cls(
            run_id=r.run_id,
            run_at=r.run_at,
            counts=r.counts(),
            errors=dict(r.errors),
            items=[ReconciliationItemOut.from_domain(i) for i in r.items],
        )
