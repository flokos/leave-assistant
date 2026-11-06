"""Calendar records (doc 07 §4.7) and the values exchanged with providers (doc 10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from leave_assistant.domain.calendar.model import (
    ConnectionKind,
    ConnectionStatus,
    ErrorClass,
    EventStatus,
    Finding,
    ItemStatus,
    SyncOperation,
    SyncStatus,
)

JOB_CALENDAR_SYNC = "calendar.sync"
JOB_CALENDAR_RECONCILE = "calendar.reconcile"


@dataclass(slots=True)
class CalendarConnection:
    id: UUID
    tenant_id: UUID
    kind: ConnectionKind
    employee_id: UUID | None  # personal only
    provider: str  # google | mock
    status: ConnectionStatus
    authorised_by_employee_id: UUID
    created_at: datetime
    updated_at: datetime
    external_calendar_id: str | None = None
    refresh_token_encrypted: bytes | None = None
    refresh_token_dek: bytes | None = None
    scopes: tuple[str, ...] = ()
    last_success_at: datetime | None = None
    last_error: str | None = None
    version: int = 1


@dataclass(slots=True)
class CalendarSyncJob:
    id: UUID
    tenant_id: UUID
    request_id: UUID
    connection_id: UUID
    operation: SyncOperation
    status: SyncStatus
    idempotency_key: str
    payload_hash: str
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    unknown_errors: int = 0
    next_attempt_at: datetime | None = None
    error_class: ErrorClass | None = None
    last_error: str | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class CalendarEventRecord:
    """Mapping between a request and the event the app wrote in a connection's calendar."""

    id: UUID
    tenant_id: UUID
    request_id: UUID
    connection_id: UUID
    external_event_id: str
    payload_hash: str
    status: EventStatus
    created_at: datetime
    updated_at: datetime
    last_verified_at: datetime | None = None


@dataclass(slots=True)
class ReconciliationItem:
    id: UUID
    tenant_id: UUID
    connection_id: UUID
    run_id: UUID
    run_at: datetime
    finding: Finding
    status: ItemStatus
    request_id: UUID | None = None
    external_event_id: str | None = None
    action: str | None = None  # what was done: enqueued_create, enqueued_update, enqueued_delete, relinked, …
    details: dict[str, Any] = field(default_factory=dict)
    resolved_by_employee_id: UUID | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OAuthState:
    """A one-time OAuth `state`: only its SHA-256 is stored; the callback re-binds the initiating principal."""

    nonce_hash: str
    tenant_id: UUID
    user_id: str
    employee_id: UUID
    kind: ConnectionKind
    provider: str
    roles: tuple[str, ...]
    expires_at: datetime
    created_at: datetime
    used_at: datetime | None = None


# --- provider values -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalendarTarget:
    """Everything a provider call needs: the decrypted refresh token lives only for the call."""

    tenant_id: UUID
    connection_id: UUID
    calendar_id: str
    refresh_token: str


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    event_id: str
    body: dict[str, Any]

    @property
    def private(self) -> dict[str, str]:
        props = self.body.get("extendedProperties") or {}
        return dict(props.get("private") or {})


@dataclass(frozen=True, slots=True)
class OAuthGrant:
    refresh_token: str
    scopes: tuple[str, ...]


class CalendarProviderError(Exception):
    """A classified provider failure (doc 10 §4.1). Messages carry status codes and reasons, never payloads."""

    def __init__(self, error_class: ErrorClass, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.retry_after = retry_after
