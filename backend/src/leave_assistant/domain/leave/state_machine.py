"""Leave request state machine (doc 07 §5.1 with the M2 modification semantics) — a table, not code paths."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from leave_assistant.domain.errors import DomainError


class RequestStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_MANAGER = "PENDING_MANAGER"
    PENDING_HR = "PENDING_HR"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    WITHDRAWN = "WITHDRAWN"
    MODIFICATION_PENDING = "MODIFICATION_PENDING"


class RequestEvent(StrEnum):
    SUBMIT = "submit"
    ROUTE_MANAGER = "route_manager"
    ROUTE_HR = "route_hr"
    AUTO_APPROVE = "auto_approve"
    WITHDRAW = "withdraw"
    MANAGER_APPROVED = "manager_approved"  # manager step approved, HR step follows
    FINAL_APPROVE = "final_approve"
    REJECT = "reject"
    CANCEL = "cancel"
    CANCEL_ACTIVE = "cancel_active"  # HR only, partial reversal
    MODIFY = "modify"
    START = "start"
    COMPLETE = "complete"
    MODIFICATION_APPROVED = "modification_approved"  # original superseded by the approved modification
    MODIFICATION_REJECTED = "modification_rejected"  # original restored


S, E = RequestStatus, RequestEvent
TRANSITIONS: Final[dict[tuple[RequestStatus, RequestEvent], RequestStatus]] = {
    (S.DRAFT, E.SUBMIT): S.SUBMITTED,
    (S.SUBMITTED, E.ROUTE_MANAGER): S.PENDING_MANAGER,
    (S.SUBMITTED, E.ROUTE_HR): S.PENDING_HR,
    (S.SUBMITTED, E.AUTO_APPROVE): S.APPROVED,
    (S.SUBMITTED, E.WITHDRAW): S.WITHDRAWN,
    (S.PENDING_MANAGER, E.MANAGER_APPROVED): S.PENDING_HR,
    (S.PENDING_MANAGER, E.FINAL_APPROVE): S.APPROVED,
    (S.PENDING_MANAGER, E.REJECT): S.REJECTED,
    (S.PENDING_MANAGER, E.CANCEL): S.CANCELLED,
    (S.PENDING_MANAGER, E.MODIFY): S.SUBMITTED,
    (S.PENDING_HR, E.FINAL_APPROVE): S.APPROVED,
    (S.PENDING_HR, E.REJECT): S.REJECTED,
    (S.PENDING_HR, E.CANCEL): S.CANCELLED,
    (S.PENDING_HR, E.MODIFY): S.SUBMITTED,
    (S.APPROVED, E.START): S.ACTIVE,
    (S.APPROVED, E.MODIFY): S.MODIFICATION_PENDING,
    (S.APPROVED, E.CANCEL): S.CANCELLED,
    (S.ACTIVE, E.COMPLETE): S.COMPLETED,
    (S.ACTIVE, E.CANCEL_ACTIVE): S.CANCELLED,
    (S.MODIFICATION_PENDING, E.MODIFICATION_APPROVED): S.CANCELLED,
    (S.MODIFICATION_PENDING, E.MODIFICATION_REJECTED): S.APPROVED,
}

PENDING_STATUSES: Final[frozenset[RequestStatus]] = frozenset({S.SUBMITTED, S.PENDING_MANAGER, S.PENDING_HR})
TERMINAL_STATUSES: Final[frozenset[RequestStatus]] = frozenset(
    {S.REJECTED, S.CANCELLED, S.WITHDRAWN, S.COMPLETED}
)
# Statuses that block overlapping requests of the same employee (mirrors the DB exclusion constraint).
BLOCKING_STATUSES: Final[frozenset[RequestStatus]] = frozenset(
    {S.DRAFT, S.SUBMITTED, S.PENDING_MANAGER, S.PENDING_HR, S.APPROVED, S.ACTIVE, S.COMPLETED}
)
DEDUCTED_STATUSES: Final[frozenset[RequestStatus]] = frozenset({S.APPROVED, S.ACTIVE, S.MODIFICATION_PENDING})


class InvalidTransition(DomainError):
    code = "leave/invalid-state"

    def __init__(self, status: RequestStatus, event: RequestEvent) -> None:
        super().__init__(
            f"event {event.value!r} is not allowed in status {status.value}",
            params={"status": status.value, "event": event.value},
        )
        self.status = status
        self.event = event


def transition(status: RequestStatus, event: RequestEvent) -> RequestStatus:
    try:
        return TRANSITIONS[(status, event)]
    except KeyError as exc:
        raise InvalidTransition(status, event) from exc
