"""FR-LEAVE-04: validation produces every doc 09 problem code; documents are a warning in M2 (decision 3)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from leave_assistant.domain.leave.request import LeaveRequest
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.leave.types import GrantMode, LeavePolicy, LeaveType
from leave_assistant.domain.leave.validation import PeriodBalance, ValidationContext, validate_request

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 5)
TENANT = UUID(int=9)
EMPLOYEE = UUID(int=21)
MON_FRI = (0, 1, 2, 3, 4)
HOLIDAYS = {date(2026, 12, 25): "Christmas", date(2026, 12, 26): "Boxing"}


def leave_type(code: str, **kw: object) -> LeaveType:
    base = dict(
        id=uuid4(),
        tenant_id=TENANT,
        code=code,
        name_el=code,
        name_en=code,
        balance_bearing=code != "unpaid",
        allow_retroactive_days=30 if code == "sick" else 0,
        requires_document_above_days=3 if code == "sick" else None,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(kw)
    return LeaveType(**base)  # type: ignore[arg-type]


def policy(leave: LeaveType, **kw: object) -> LeavePolicy:
    base = dict(
        id=uuid4(),
        tenant_id=TENANT,
        leave_type_id=leave.id,
        year=2026,
        entitlement_days=Decimal("20"),
        carryover_max_days=Decimal("5"),
        carryover_expires_on=date(2027, 3, 31),
        allow_negative=False,
        min_notice_days=0,
        max_consecutive_days=None,
        prorate_on_hire=True,
        grant_mode=GrantMode.UPFRONT,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(kw)
    return LeavePolicy(**base)  # type: ignore[arg-type]


def existing(
    start: date, end: date, status: RequestStatus = RequestStatus.APPROVED, number: str = "LR-2026-000100"
) -> LeaveRequest:
    return LeaveRequest(
        id=uuid4(),
        tenant_id=TENANT,
        request_number=number,
        employee_id=EMPLOYEE,
        leave_type_id=uuid4(),
        start_date=start,
        end_date=end,
        working_days=Decimal("1"),
        status=status,
        reason=None,
        submitted_at=NOW,
        decided_at=None,
        cancelled_at=None,
        modification_of_request_id=None,
        idempotency_key="k",
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def ctx(
    leave: LeaveType,
    pol: LeavePolicy | None = None,
    *,
    balances: dict[int, PeriodBalance] | None = None,
    **kw: object,
) -> ValidationContext:
    base = dict(
        today=TODAY,
        leave_type=leave,
        policy=pol,
        work_week=MON_FRI,
        holidays=HOLIDAYS,
        existing_requests=(),
        balances=balances or {2026: PeriodBalance(Decimal("12"), Decimal("0"))},
        has_document=False,
    )
    base.update(kw)
    return ValidationContext(**base)  # type: ignore[arg-type]


def codes(report) -> list[str]:  # type: ignore[no-untyped-def]
    return [v.code for v in report.violations]


def test_valid_annual_request_reports_balance_after() -> None:
    annual = leave_type("annual")
    report = validate_request(date(2026, 12, 14), date(2026, 12, 18), ctx(annual, policy(annual)))
    assert report.valid and report.working_days == 5 and codes(report) == []
    assert report.balance is not None and (report.balance.available_days, report.balance.after_days) == (
        Decimal("12"),
        Decimal("7"),
    )


def test_invalid_dates_and_no_working_days() -> None:
    annual = leave_type("annual")
    assert codes(validate_request(date(2026, 12, 18), date(2026, 12, 14), ctx(annual))) == [
        "leave/invalid-dates"
    ]
    assert codes(validate_request(date(2026, 12, 26), date(2026, 12, 27), ctx(annual))) == [
        "leave/invalid-dates"
    ]  # holiday + weekend


def test_retroactive_rules() -> None:
    annual, sick = leave_type("annual"), leave_type("sick")
    assert codes(validate_request(date(2026, 11, 2), date(2026, 11, 3), ctx(annual))) == [
        "leave/retroactive-not-allowed"
    ]
    assert codes(validate_request(date(2026, 11, 2), date(2026, 11, 3), ctx(sick))) == []
    assert codes(validate_request(date(2026, 9, 1), date(2026, 9, 2), ctx(sick))) == [
        "leave/retroactive-not-allowed"
    ]


def test_overlap_with_open_requests_only() -> None:
    annual = leave_type("annual")
    blocking = existing(date(2026, 12, 16), date(2026, 12, 17))
    cancelled = existing(date(2026, 12, 16), date(2026, 12, 17), RequestStatus.CANCELLED)
    report = validate_request(
        date(2026, 12, 14), date(2026, 12, 18), ctx(annual, existing_requests=(blocking,))
    )
    assert (
        codes(report) == ["leave/overlap"]
        and report.violations[0].params["request_number"] == "LR-2026-000100"
    )
    assert (
        codes(
            validate_request(
                date(2026, 12, 14), date(2026, 12, 18), ctx(annual, existing_requests=(cancelled,))
            )
        )
        == []
    )


def test_notice_period_and_max_consecutive() -> None:
    annual = leave_type("annual")
    pol = policy(annual, min_notice_days=3, max_consecutive_days=4)
    report = validate_request(date(2026, 11, 6), date(2026, 11, 12), ctx(annual, pol))
    assert codes(report) == ["leave/notice-period", "leave/max-consecutive"]


def test_insufficient_balance_and_unpaid_is_not_balance_bearing() -> None:
    annual, unpaid = leave_type("annual"), leave_type("unpaid")
    short = {2026: PeriodBalance(Decimal("3"), Decimal("1"))}  # available 2
    report = validate_request(
        date(2026, 12, 14), date(2026, 12, 16), ctx(annual, policy(annual), balances=short)
    )
    assert codes(report) == ["leave/insufficient-balance"] and report.violations[0].params == {
        "available": "2",
        "requested": "3",
        "period": 2026,
    }
    allow_negative = policy(annual, allow_negative=True)
    assert validate_request(
        date(2026, 12, 14), date(2026, 12, 16), ctx(annual, allow_negative, balances=short)
    ).valid
    assert validate_request(date(2026, 12, 14), date(2026, 12, 16), ctx(unpaid, balances=short)).valid


def test_request_spanning_years_is_checked_per_period() -> None:
    annual = leave_type("annual")
    balances = {
        2026: PeriodBalance(Decimal("2"), Decimal("0")),
        2027: PeriodBalance(Decimal("0"), Decimal("0")),
    }
    report = validate_request(
        date(2026, 12, 28), date(2027, 1, 5), ctx(annual, policy(annual), balances=balances)
    )
    assert report.working_days == 7
    assert [(v.code, v.params["period"]) for v in report.violations] == [
        ("leave/insufficient-balance", 2026),
        ("leave/insufficient-balance", 2027),
    ]
    assert [(p.year, p.working_days) for p in report.periods] == [(2026, 4), (2027, 3)]


def test_sick_leave_document_is_a_warning_in_m2() -> None:
    sick = leave_type("sick")
    report = validate_request(date(2026, 11, 2), date(2026, 11, 6), ctx(sick, policy(sick)))
    assert report.valid and codes(report) == []
    assert [w.code for w in report.warnings] == ["leave/document-required"]
    documented = validate_request(
        date(2026, 11, 2), date(2026, 11, 6), ctx(sick, policy(sick), has_document=True)
    )
    assert documented.warnings == ()


@pytest.mark.parametrize("today", [date(2026, 11, 5), date(2026, 12, 31)])
def test_today_is_allowed_as_start(today: date) -> None:
    annual = leave_type("annual")
    assert (
        validate_request(today, today, ctx(annual, policy(annual), today=today, holidays={})).valid
        or today.weekday() >= 5
    )
