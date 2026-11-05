"""Ledger entries and balance arithmetic (ADR-0005, Q-01): append-only, signed quantities, idempotent keys."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import UUID

ZERO = Decimal("0")
HALF = Decimal("0.5")


class EntryType(StrEnum):
    GRANT = "GRANT"
    DEDUCTION = "DEDUCTION"
    REVERSAL = "REVERSAL"
    ADJUSTMENT = "ADJUSTMENT"
    CARRYOVER = "CARRYOVER"
    EXPIRY = "EXPIRY"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    id: UUID
    tenant_id: UUID
    employee_id: UUID
    leave_type_id: UUID
    period_year: int
    entry_type: EntryType
    quantity_days: Decimal  # signed
    effective_date: date
    idempotency_key: str
    created_at: datetime
    request_id: UUID | None = None
    reason: str | None = None
    created_by_employee_id: UUID | None = None


def balance_of(quantities: Iterable[Decimal]) -> Decimal:
    return sum(quantities, ZERO)


def round_half_day(value: Decimal) -> Decimal:
    """Round to the nearest 0.5 day, halves up (ADR-0028: 0.5-day rounding of pro-rated grants)."""
    return (value * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2


def prorated_entitlement(entitlement: Decimal, *, hire_date: date, year: int, prorate: bool) -> Decimal:
    """Remaining full months / 12 × entitlement for hires during `year`; full entitlement for earlier hires."""
    if hire_date.year < year or not prorate and hire_date.year == year:
        return entitlement
    if hire_date.year > year:
        return ZERO
    remaining_months = 12 - hire_date.month + (1 if hire_date.day == 1 else 0)
    return round_half_day(entitlement * Decimal(remaining_months) / Decimal(12))


def carryover_amount(balance: Decimal, *, cap: Decimal) -> Decimal:
    return max(ZERO, min(balance, cap))


def grant_key(employee_id: UUID, leave_type_id: UUID, year: int) -> str:
    return f"grant:{employee_id}:{leave_type_id}:{year}"


def carryover_key(employee_id: UUID, leave_type_id: UUID, year: int) -> str:
    return f"carryover:{employee_id}:{leave_type_id}:{year}"


def expiry_key(employee_id: UUID, leave_type_id: UUID, year: int) -> str:
    return f"expiry:{employee_id}:{leave_type_id}:{year}"


def deduction_key(request_id: UUID, period_year: int) -> str:
    return f"deduction:{request_id}:{period_year}"


def reversal_key(request_id: UUID, period_year: int, *, suffix: str = "") -> str:
    return f"reversal:{request_id}:{period_year}{(':' + suffix) if suffix else ''}"


def adjustment_key(adjustment_id: UUID) -> str:
    return f"adjustment:{adjustment_id}"
