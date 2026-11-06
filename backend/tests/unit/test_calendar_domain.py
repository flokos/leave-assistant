"""Calendar domain (doc 10): privacy of the event payload, deterministic ids, backoff and job transitions."""

from __future__ import annotations

import random
import re
from datetime import date
from uuid import UUID, uuid4

import pytest

from leave_assistant.domain.calendar.model import (
    BACKOFF_SECONDS,
    ConnectionKind,
    ErrorClass,
    SyncOperation,
    SyncStatus,
    build_payload,
    check_transition,
    event_id,
    idempotency_key,
    next_delay,
    outcome_for,
)
from leave_assistant.domain.errors import InvalidValue

TENANT = UUID(int=1)
REQUEST = UUID("01a1b0ff-0000-7000-8000-000000000042")


def test_company_events_carry_the_name_personal_events_do_not_and_neither_carries_the_leave_type() -> None:
    company = build_payload(
        kind=ConnectionKind.COMPANY_SHARED, tenant_id=TENANT, request_id=REQUEST,
        display_name="Ελένη Δημητρίου", locale="el", start=date(2026, 12, 14), end=date(2026, 12, 18),
    )  # fmt: skip
    personal = build_payload(
        kind=ConnectionKind.PERSONAL, tenant_id=TENANT, request_id=REQUEST,
        display_name="Ελένη Δημητρίου", locale="en", start=date(2026, 12, 14), end=date(2026, 12, 18),
    )  # fmt: skip
    assert company.summary == "Ελένη Δημητρίου — Εκτός γραφείου" and company.transparency == "transparent"
    assert personal.summary == "Out of office" and personal.transparency == "opaque"
    body = company.body()
    assert body["end"] == {"date": "2026-12-19"}  # exclusive end
    flat = repr(body).lower()
    for forbidden in ("annual", "sick", "unpaid", "reason", "department", "κανονική", "αναρρωτική"):
        assert forbidden not in flat
    assert body["extendedProperties"]["private"] == {
        "app": "leave-assistant",
        "tenant_id": str(TENANT),
        "leave_request_id": str(REQUEST),
    }


def test_event_ids_are_deterministic_google_valid_and_differ_per_calendar_kind() -> None:
    company, personal = (
        event_id(REQUEST, ConnectionKind.COMPANY_SHARED),
        event_id(REQUEST, ConnectionKind.PERSONAL),
    )
    assert company == event_id(REQUEST, ConnectionKind.COMPANY_SHARED) and company != personal
    for value in (company, personal):
        assert re.fullmatch(r"[a-v0-9]{5,1024}", value)


def test_idempotency_keys_change_with_the_payload() -> None:
    a = idempotency_key(REQUEST, TENANT, SyncOperation.CREATE, "h1")
    assert a == idempotency_key(REQUEST, TENANT, SyncOperation.CREATE, "h1")
    assert a != idempotency_key(REQUEST, TENANT, SyncOperation.CREATE, "h2")
    assert a != idempotency_key(REQUEST, TENANT, SyncOperation.DELETE, "h1")


def test_backoff_follows_doc_10_and_honours_retry_after() -> None:
    rng = random.Random(1)
    delays = [next_delay(n, jitter=rng) for n in range(1, 6)]
    for delay, base in zip(delays, BACKOFF_SECONDS, strict=True):
        assert base * 0.9 <= delay <= base * 1.1
    assert next_delay(1, retry_after=600, jitter=rng) == 600


@pytest.mark.parametrize(
    ("error", "attempts", "unknown_seen", "expected"),
    [
        (ErrorClass.TRANSIENT, 1, 0, SyncStatus.PENDING),
        (ErrorClass.TRANSIENT, 5, 0, SyncStatus.DEAD),
        (ErrorClass.AUTH_EXPIRED, 1, 0, SyncStatus.PARKED),
        (ErrorClass.PERMANENT, 1, 0, SyncStatus.DEAD),
        (ErrorClass.UNKNOWN, 1, 1, SyncStatus.PENDING),
        (ErrorClass.UNKNOWN, 2, 2, SyncStatus.DEAD),
    ],
)
def test_failed_attempt_outcomes(
    error: ErrorClass, attempts: int, unknown_seen: int, expected: SyncStatus
) -> None:
    assert outcome_for(error, attempts, unknown_seen) is expected


def test_job_transitions() -> None:
    check_transition(SyncStatus.PENDING, SyncStatus.RUNNING)
    check_transition(SyncStatus.PARKED, SyncStatus.PENDING)
    check_transition(SyncStatus.DEAD, SyncStatus.PENDING)
    with pytest.raises(InvalidValue):
        check_transition(SyncStatus.SUCCEEDED, SyncStatus.PENDING)


def test_wire_body_carries_the_hash_of_the_plain_body() -> None:
    from leave_assistant.domain.calendar.model import stored_hash

    payload = build_payload(
        kind=ConnectionKind.PERSONAL,
        tenant_id=uuid4(),
        request_id=uuid4(),
        display_name="Maria Papadopoulou",
        locale="en",
        start=date(2026, 12, 14),
        end=date(2026, 12, 18),
    )
    wire = payload.wire_body()
    assert stored_hash(wire) == payload.hash
    assert "payload_hash" not in payload.body()["extendedProperties"]["private"]
    assert stored_hash({}) is None
