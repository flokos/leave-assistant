"""Doc 07 §5.1 transition table (with the M2 modification semantics) — every other (state, event) pair is invalid."""

from __future__ import annotations

import itertools

import pytest

from leave_assistant.domain.leave.state_machine import (
    BLOCKING_STATUSES,
    PENDING_STATUSES,
    TERMINAL_STATUSES,
    InvalidTransition,
    RequestEvent,
    RequestStatus,
    transition,
)

S, E = RequestStatus, RequestEvent
TABLE: dict[tuple[RequestStatus, RequestEvent], RequestStatus] = {
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


@pytest.mark.parametrize(("state", "event", "target"), [(s, e, t) for (s, e), t in TABLE.items()])
def test_allowed_transition(state: RequestStatus, event: RequestEvent, target: RequestStatus) -> None:
    assert transition(state, event) is target


@pytest.mark.parametrize(
    ("state", "event"),
    [(s, e) for s, e in itertools.product(RequestStatus, RequestEvent) if (s, e) not in TABLE],
)
def test_other_pairs_are_invalid(state: RequestStatus, event: RequestEvent) -> None:
    with pytest.raises(InvalidTransition):
        transition(state, event)


def test_status_sets() -> None:
    assert PENDING_STATUSES == {S.SUBMITTED, S.PENDING_MANAGER, S.PENDING_HR}
    assert TERMINAL_STATUSES == {S.REJECTED, S.CANCELLED, S.WITHDRAWN, S.COMPLETED}
    assert S.MODIFICATION_PENDING not in BLOCKING_STATUSES and S.APPROVED in BLOCKING_STATUSES
    assert S.CANCELLED not in BLOCKING_STATUSES
