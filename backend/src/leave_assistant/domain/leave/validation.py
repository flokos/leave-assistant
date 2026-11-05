"""Request validation (FR-LEAVE-04): a pure function over an explicit context; codes are doc 09 problem types."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from leave_assistant.domain.leave.request import LeaveRequest
from leave_assistant.domain.leave.state_machine import BLOCKING_STATUSES
from leave_assistant.domain.leave.types import LeavePolicy, LeaveType, StepRole
from leave_assistant.domain.leave.working_days import ExcludedDate, classify, split_by_year

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    detail: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PeriodBalance:
    balance_days: Decimal
    pending_days: Decimal

    @property
    def available_days(self) -> Decimal:
        return self.balance_days - self.pending_days


@dataclass(frozen=True, slots=True)
class PeriodDemand:
    year: int
    start: date
    end: date
    working_days: int


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    balance_days: Decimal
    pending_days: Decimal
    available_days: Decimal
    after_days: Decimal


@dataclass(frozen=True, slots=True)
class ValidationContext:
    today: date
    leave_type: LeaveType
    policy: LeavePolicy | None
    work_week: Sequence[int]
    holidays: Mapping[date, str]
    existing_requests: Sequence[LeaveRequest]  # the employee's requests, excluding the one being modified
    balances: Mapping[int, PeriodBalance]  # per period year, for balance-bearing types
    has_document: bool = False


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    working_days: int
    excluded_dates: tuple[ExcludedDate, ...]
    periods: tuple[PeriodDemand, ...]
    balance: BalanceSnapshot | None
    violations: tuple[Violation, ...]
    warnings: tuple[Violation, ...]
    approval_chain: tuple[StepRole, ...] = ()
    auto_acknowledged: bool = False

    def with_routing(self, chain: tuple[StepRole, ...], auto_acknowledged: bool) -> ValidationReport:
        return ValidationReport(
            valid=self.valid,
            working_days=self.working_days,
            excluded_dates=self.excluded_dates,
            periods=self.periods,
            balance=self.balance,
            violations=self.violations,
            warnings=self.warnings,
            approval_chain=chain,
            auto_acknowledged=auto_acknowledged,
        )


def validate_request(start: date, end: date, ctx: ValidationContext) -> ValidationReport:
    violations: list[Violation] = []
    warnings: list[Violation] = []
    if end < start:
        return ValidationReport(
            False, 0, (), (), None, (Violation("leave/invalid-dates", "end_date is before start_date"),), ()
        )

    days = classify(start, end, work_week=ctx.work_week, holidays=ctx.holidays)
    periods = tuple(
        PeriodDemand(
            seg_start.year,
            seg_start,
            seg_end,
            classify(seg_start, seg_end, work_week=ctx.work_week, holidays=ctx.holidays).count,
        )
        for seg_start, seg_end in split_by_year(start, end)
    )
    if days.count == 0:
        violations.append(Violation("leave/invalid-dates", "the range contains no working days"))

    if start < ctx.today:
        back = (ctx.today - start).days
        if ctx.leave_type.allow_retroactive_days <= 0 or back > ctx.leave_type.allow_retroactive_days:
            violations.append(
                Violation(
                    "leave/retroactive-not-allowed",
                    "requests in the past are not allowed for this leave type",
                    {"allow_retroactive_days": ctx.leave_type.allow_retroactive_days, "days_back": back},
                )
            )

    for other in ctx.existing_requests:
        if other.status in BLOCKING_STATUSES and other.overlaps(start, end):
            violations.append(
                Violation(
                    "leave/overlap",
                    f"the dates overlap with request {other.request_number}",
                    {"request_number": other.request_number},
                )
            )
            break

    if ctx.policy is not None:
        if ctx.policy.min_notice_days > 0 and (start - ctx.today).days < ctx.policy.min_notice_days:
            violations.append(
                Violation(
                    "leave/notice-period",
                    "the request does not respect the notice period",
                    {"min_notice_days": ctx.policy.min_notice_days},
                )
            )
        if ctx.policy.max_consecutive_days is not None and days.count > ctx.policy.max_consecutive_days:
            violations.append(
                Violation(
                    "leave/max-consecutive",
                    "the request exceeds the maximum consecutive working days",
                    {"max_consecutive_days": ctx.policy.max_consecutive_days, "working_days": days.count},
                )
            )

    balance: BalanceSnapshot | None = None
    if ctx.leave_type.balance_bearing:
        allow_negative = bool(ctx.policy and ctx.policy.allow_negative)
        total_balance = total_pending = total_available = ZERO
        for period in periods:
            period_balance = ctx.balances.get(period.year, PeriodBalance(ZERO, ZERO))
            total_balance += period_balance.balance_days
            total_pending += period_balance.pending_days
            total_available += period_balance.available_days
            if not allow_negative and period_balance.available_days - period.working_days < 0:
                violations.append(
                    Violation(
                        "leave/insufficient-balance",
                        "insufficient available balance for this period",
                        {
                            "available": str(period_balance.available_days),
                            "requested": str(period.working_days),
                            "period": period.year,
                        },
                    )
                )
        balance = BalanceSnapshot(total_balance, total_pending, total_available, total_available - days.count)

    threshold = ctx.leave_type.requires_document_above_days
    if threshold is not None and days.count > threshold and not ctx.has_document:
        warnings.append(
            Violation(
                "leave/document-required",
                "a supporting document is required above the threshold",
                {"above_days": threshold},
            )
        )

    return ValidationReport(
        valid=not violations,
        working_days=days.count,
        excluded_dates=days.excluded,
        periods=periods,
        balance=balance,
        violations=tuple(violations),
        warnings=tuple(warnings),
    )
