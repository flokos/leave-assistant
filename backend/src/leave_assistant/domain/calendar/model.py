"""Calendar synchronisation domain (doc 10, ADR-0020): what an absence looks like in a calendar, how a sync job moves
through its states and how provider errors are classified. Pure — no I/O.

Privacy by design (brief §10.1): an event exposes the employee's name (company calendar only), the dates and the
availability — never the leave type, the reason, documents, department or approvers.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from leave_assistant.domain.errors import InvalidValue

APP_MARKER = "leave-assistant"
BACKOFF_SECONDS: tuple[int, ...] = (60, 300, 900, 3600, 14400)  # 1 min, 5 min, 15 min, 1 h, 4 h
MAX_ATTEMPTS = len(BACKOFF_SECONDS)
UNKNOWN_AS_TRANSIENT = 2  # an unclassified error is retried twice, then treated as permanent


class ConnectionKind(StrEnum):
    COMPANY_SHARED = "company_shared"
    PERSONAL = "personal"


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"  # the refresh token stopped working — re-authorise
    REVOKED = "revoked"


class SyncOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class SyncStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARKED = "parked"  # the connection expired; resumes on re-authorisation
    DEAD = "dead"  # non-retryable or attempts exhausted; a manual retry puts it back to pending


class ErrorClass(StrEnum):
    TRANSIENT = "transient"  # 5xx, 429, network, timeouts
    AUTH_EXPIRED = "auth_expired"  # invalid_grant, 401
    PERMANENT = "permanent"  # 400/403/404 on the calendar
    UNKNOWN = "unknown"


class EventStatus(StrEnum):
    PRESENT = "present"
    DELETED = "deleted"
    ORPHANED = "orphaned"  # the connection was revoked; the event stays in the owner's calendar
    MISSING = "missing"


class Finding(StrEnum):
    MISSING = "missing"
    MISMATCHED = "mismatched"
    ORPHANED = "orphaned"
    UNKNOWN_EXTERNAL_ID = "unknown_external_id"


class ItemStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


TRANSITIONS: Mapping[SyncStatus, frozenset[SyncStatus]] = {
    SyncStatus.PENDING: frozenset({SyncStatus.RUNNING, SyncStatus.PARKED, SyncStatus.DEAD}),
    SyncStatus.RUNNING: frozenset(
        {SyncStatus.SUCCEEDED, SyncStatus.PENDING, SyncStatus.PARKED, SyncStatus.DEAD}
    ),
    SyncStatus.PARKED: frozenset({SyncStatus.PENDING, SyncStatus.DEAD}),
    SyncStatus.DEAD: frozenset({SyncStatus.PENDING}),  # manual retry
    SyncStatus.SUCCEEDED: frozenset(),
}


def check_transition(current: SyncStatus, target: SyncStatus) -> None:
    if target not in TRANSITIONS[current]:
        raise InvalidValue(
            f"a {current.value} sync job cannot become {target.value}",
            code="calendar/invalid-transition",
            params={"from": current.value, "to": target.value},
        )


@dataclass(frozen=True, slots=True)
class EventPayload:
    """What the provider receives. `private` is stored in `extendedProperties.private` (ownership marker)."""

    event_id: str
    summary: str
    description: str
    start: date
    end_exclusive: date  # all-day events: the end date is exclusive in the Calendar API
    transparency: str  # transparent (company calendar) | opaque (personal: shows the employee as busy)
    private: dict[str, str] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "summary": self.summary,
            "description": self.description,
            "start": {"date": self.start.isoformat()},
            "end": {"date": self.end_exclusive.isoformat()},
            "transparency": self.transparency,
            "visibility": "default",
            "extendedProperties": {"private": dict(self.private)},
        }

    @property
    def hash(self) -> str:
        return payload_hash(self.body())

    def wire_body(self) -> dict[str, Any]:
        """The body sent to the provider: the content hash travels in the private properties (reconciliation
        compares it without re-deriving Google's normalised representation)."""
        body = self.body()
        body["extendedProperties"]["private"]["payload_hash"] = self.hash
        return body


def stored_hash(body: Mapping[str, Any]) -> str | None:
    private = (body.get("extendedProperties") or {}).get("private") or {}
    value = private.get("payload_hash")
    return str(value) if value else None


SUMMARY = {
    "en": {ConnectionKind.COMPANY_SHARED: "{name} — Out of office", ConnectionKind.PERSONAL: "Out of office"},
    "el": {
        ConnectionKind.COMPANY_SHARED: "{name} — Εκτός γραφείου",
        ConnectionKind.PERSONAL: "Εκτός γραφείου",
    },
}
DESCRIPTION = {
    "en": "Approved absence. Availability: unavailable.",
    "el": "Εγκεκριμένη απουσία. Διαθεσιμότητα: μη διαθέσιμος/η.",
}


def event_id(request_id: UUID, kind: ConnectionKind) -> str:
    """Deterministic, Google-valid id (base32hex → `[a-v0-9]`): re-running a create finds the same event (409)."""
    raw = request_id.bytes + (b"c" if kind is ConnectionKind.COMPANY_SHARED else b"p")
    return "la" + base64.b32hexencode(raw).decode().rstrip("=").lower()


def build_payload(
    *,
    kind: ConnectionKind,
    tenant_id: UUID,
    request_id: UUID,
    display_name: str,
    locale: str,
    start: date,
    end: date,
) -> EventPayload:
    texts = SUMMARY.get(locale, SUMMARY["en"])
    return EventPayload(
        event_id=event_id(request_id, kind),
        summary=texts[kind].format(name=display_name),
        description=DESCRIPTION.get(locale, DESCRIPTION["en"]),
        start=start,
        end_exclusive=end + timedelta(days=1),
        transparency="transparent" if kind is ConnectionKind.COMPANY_SHARED else "opaque",
        private={"app": APP_MARKER, "tenant_id": str(tenant_id), "leave_request_id": str(request_id)},
    )


def payload_hash(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def idempotency_key(request_id: UUID, connection_id: UUID, operation: SyncOperation, payload: str) -> str:
    raw = f"{request_id}:{connection_id}:{operation.value}:{payload}"
    return hashlib.sha256(raw.encode()).hexdigest()


def next_delay(
    attempts: int, *, retry_after: float | None = None, jitter: random.Random | None = None
) -> float:
    """Seconds until the next attempt after `attempts` failed ones (1-based); `Retry-After` wins when longer."""
    base = BACKOFF_SECONDS[min(max(attempts, 1), MAX_ATTEMPTS) - 1]
    spread = (jitter or random).uniform(0.9, 1.1)  # noqa: S311 - jitter only
    delay = base * spread
    return max(delay, retry_after or 0.0)


def outcome_for(error: ErrorClass, attempts: int, unknown_seen: int = 0) -> SyncStatus:
    """Where a failed attempt leaves the job (doc 10 §4.1)."""
    if error is ErrorClass.AUTH_EXPIRED:
        return SyncStatus.PARKED
    if error is ErrorClass.PERMANENT:
        return SyncStatus.DEAD
    if error is ErrorClass.UNKNOWN and unknown_seen >= UNKNOWN_AS_TRANSIENT:
        return SyncStatus.DEAD
    return SyncStatus.DEAD if attempts >= MAX_ATTEMPTS else SyncStatus.PENDING
