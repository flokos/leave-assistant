"""Document version lifecycle (doc 05 §2) — a transition table, like the leave state machine."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from leave_assistant.domain.documents.types import VersionStatus
from leave_assistant.domain.errors import DomainError


class VersionEvent(StrEnum):
    PROCESS = "process"  # worker claims the job
    FAIL = "fail"  # parse/OCR error after the retries
    ACTIVATE = "activate"  # risk low
    SEND_TO_REVIEW = "send_to_review"  # risk medium
    QUARANTINE = "quarantine"  # risk high
    RELEASE = "release"  # reviewer releases
    REJECT = "reject"  # reviewer rejects a pending version
    DELETE = "delete"
    SUPERSEDE = "supersede"  # a newer version became active
    RETIRE = "retire"  # owner retires the active version
    REPROCESS = "reprocess"  # re-run the pipeline after a failure


S, E = VersionStatus, VersionEvent
TRANSITIONS: Final[dict[tuple[VersionStatus, VersionEvent], VersionStatus]] = {
    (S.UPLOADED, E.PROCESS): S.PROCESSING,
    (S.PROCESSING, E.FAIL): S.FAILED,
    (S.PROCESSING, E.ACTIVATE): S.ACTIVE,
    (S.PROCESSING, E.SEND_TO_REVIEW): S.PENDING_REVIEW,
    (S.PROCESSING, E.QUARANTINE): S.QUARANTINED,
    (S.PENDING_REVIEW, E.RELEASE): S.ACTIVE,
    (S.PENDING_REVIEW, E.REJECT): S.QUARANTINED,
    (S.QUARANTINED, E.RELEASE): S.ACTIVE,
    (S.QUARANTINED, E.DELETE): S.DELETED,
    (
        S.ACTIVE,
        E.SEND_TO_REVIEW,
    ): S.PENDING_REVIEW,  # M5 back-fill: first scan of a version activated before M5
    (S.ACTIVE, E.QUARANTINE): S.QUARANTINED,
    (S.ACTIVE, E.SUPERSEDE): S.SUPERSEDED,
    (S.ACTIVE, E.RETIRE): S.RETIRED,
    (S.SUPERSEDED, E.DELETE): S.DELETED,
    (S.RETIRED, E.DELETE): S.DELETED,
    (S.FAILED, E.DELETE): S.DELETED,
    (S.FAILED, E.REPROCESS): S.UPLOADED,
}
RETRIEVABLE_STATUSES: Final[frozenset[VersionStatus]] = frozenset({S.ACTIVE})
REVIEW_STATUSES: Final[frozenset[VersionStatus]] = frozenset({S.PENDING_REVIEW, S.QUARANTINED})
TERMINAL_STATUSES: Final[frozenset[VersionStatus]] = frozenset({S.DELETED})


class InvalidTransition(DomainError):
    code = "document/invalid-state"


def transition(status: VersionStatus, event: VersionEvent) -> VersionStatus:
    try:
        return TRANSITIONS[(status, event)]
    except KeyError:
        raise InvalidTransition(
            f"cannot {event.value} a version in status {status.value}",
            params={"status": status.value, "event": event.value},
        ) from None


def can(status: VersionStatus, event: VersionEvent) -> bool:
    return (status, event) in TRANSITIONS
