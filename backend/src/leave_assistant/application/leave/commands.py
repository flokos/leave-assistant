"""Commands for the leave use cases (API schemas convert to these)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.domain.leave.request import DecisionChannel
from leave_assistant.domain.leave.types import GrantMode, StepRole


@dataclass(frozen=True, slots=True)
class CreateLeaveType:
    code: str
    name_el: str
    name_en: str
    balance_bearing: bool
    allow_retroactive_days: int = 0
    requires_document_above_days: int | None = None


@dataclass(frozen=True, slots=True)
class UpdateLeaveType:
    name_el: str | None = None
    name_en: str | None = None
    balance_bearing: bool | None = None
    allow_retroactive_days: int | None = None
    requires_document_above_days: int | None = UNSET
    active: bool | None = None


@dataclass(frozen=True, slots=True)
class CreatePolicy:
    leave_type_code: str
    year: int
    entitlement_days: Decimal
    carryover_max_days: Decimal = Decimal("0")
    carryover_expires_on: date | None = None
    allow_negative: bool = False
    min_notice_days: int = 0
    max_consecutive_days: int | None = None
    prorate_on_hire: bool = True
    grant_mode: GrantMode = GrantMode.UPFRONT


@dataclass(frozen=True, slots=True)
class UpdatePolicy:
    entitlement_days: Decimal | None = None
    carryover_max_days: Decimal | None = None
    carryover_expires_on: date | None = UNSET
    allow_negative: bool | None = None
    min_notice_days: int | None = None
    max_consecutive_days: int | None = UNSET
    prorate_on_hire: bool | None = None


@dataclass(frozen=True, slots=True)
class CreateHoliday:
    holiday_date: date
    name_el: str
    name_en: str
    recurring: bool = False


@dataclass(frozen=True, slots=True)
class UpdateHoliday:
    holiday_date: date | None = None
    name_el: str | None = None
    name_en: str | None = None
    recurring: bool | None = None


@dataclass(frozen=True, slots=True)
class CreateRule:
    steps: tuple[StepRole, ...]
    leave_type_code: str | None = None
    min_working_days: Decimal = Decimal("0")
    max_working_days: Decimal | None = None
    requires_document: bool = False
    auto_acknowledge: bool = False
    priority: int = 100


@dataclass(frozen=True, slots=True)
class UpdateRule:
    steps: tuple[StepRole, ...] | None = None
    leave_type_code: str | None = UNSET
    min_working_days: Decimal | None = None
    max_working_days: Decimal | None = UNSET
    requires_document: bool | None = None
    auto_acknowledge: bool | None = None
    priority: int | None = None
    active: bool | None = None


@dataclass(frozen=True, slots=True)
class SimulateRequest:
    leave_type_code: str
    start_date: date
    end_date: date
    employee_id: UUID


@dataclass(frozen=True, slots=True)
class SubmitRequest:
    leave_type_code: str
    start_date: date
    end_date: date
    reason: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class ModifyRequest:
    start_date: date
    end_date: date
    version: int
    reason: str | None = UNSET


@dataclass(frozen=True, slots=True)
class CancelRequest:
    version: int
    comment: str | None = None


@dataclass(frozen=True, slots=True)
class Decide:
    version: int
    comment: str | None = None
    via: DecisionChannel = DecisionChannel.UI


@dataclass(frozen=True, slots=True)
class Adjustment:
    employee_id: UUID
    leave_type_code: str
    quantity_days: Decimal
    reason: str
    period_year: int | None = None
    effective_date: date | None = None


def as_dict(obj: Any) -> dict[str, Any]:  # pragma: no cover - debugging aid
    return dict(obj.__dict__)
