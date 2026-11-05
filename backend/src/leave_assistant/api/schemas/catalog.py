"""Company administration of the leave catalogue (doc 09 §2.8)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.api.schemas.leave import days
from leave_assistant.application.ports import BalanceRow
from leave_assistant.domain.leave.types import ApprovalRule, LeavePolicy, PublicHoliday

StepRoleField = Literal["manager", "hr"]


class LeaveTypeCreate(ApiModel):
    code: str = Field(min_length=1, max_length=32)
    name_el: str = Field(min_length=1, max_length=120)
    name_en: str = Field(min_length=1, max_length=120)
    balance_bearing: bool
    allow_retroactive_days: int = Field(default=0, ge=0)
    requires_document_above_days: int | None = Field(default=None, ge=0)


class LeaveTypeUpdate(ApiModel):
    name_el: str | None = Field(default=None, min_length=1, max_length=120)
    name_en: str | None = Field(default=None, min_length=1, max_length=120)
    balance_bearing: bool | None = None
    allow_retroactive_days: int | None = Field(default=None, ge=0)
    requires_document_above_days: int | None = Field(default=None, ge=0)
    active: bool | None = None


class PolicyOut(ApiModel):
    id: UUID
    leave_type_id: UUID
    year: int
    entitlement_days: float
    carryover_max_days: float
    carryover_expires_on: date | None
    allow_negative: bool
    min_notice_days: int
    max_consecutive_days: int | None
    prorate_on_hire: bool
    grant_mode: str

    @classmethod
    def from_domain(cls, p: LeavePolicy) -> PolicyOut:
        return cls(
            id=p.id,
            leave_type_id=p.leave_type_id,
            year=p.year,
            entitlement_days=days(p.entitlement_days),
            carryover_max_days=days(p.carryover_max_days),
            carryover_expires_on=p.carryover_expires_on,
            allow_negative=p.allow_negative,
            min_notice_days=p.min_notice_days,
            max_consecutive_days=p.max_consecutive_days,
            prorate_on_hire=p.prorate_on_hire,
            grant_mode=p.grant_mode.value,
        )


class PolicyCreate(ApiModel):
    leave_type: str = Field(max_length=32)
    year: int = Field(ge=2000, le=2100)
    entitlement_days: Decimal = Field(ge=0)
    carryover_max_days: Decimal = Field(default=Decimal("0"), ge=0)
    carryover_expires_on: date | None = None
    allow_negative: bool = False
    min_notice_days: int = Field(default=0, ge=0)
    max_consecutive_days: int | None = Field(default=None, ge=1)
    prorate_on_hire: bool = True


class PolicyUpdate(ApiModel):
    entitlement_days: Decimal | None = Field(default=None, ge=0)
    carryover_max_days: Decimal | None = Field(default=None, ge=0)
    carryover_expires_on: date | None = None
    allow_negative: bool | None = None
    min_notice_days: int | None = Field(default=None, ge=0)
    max_consecutive_days: int | None = Field(default=None, ge=1)
    prorate_on_hire: bool | None = None


class HolidayOut(ApiModel):
    id: UUID
    holiday_date: date
    name_el: str
    name_en: str
    recurring: bool

    @classmethod
    def from_domain(cls, h: PublicHoliday) -> HolidayOut:
        return cls(
            id=h.id, holiday_date=h.holiday_date, name_el=h.name_el, name_en=h.name_en, recurring=h.recurring
        )


class HolidayCreate(ApiModel):
    holiday_date: date
    name_el: str = Field(min_length=1, max_length=120)
    name_en: str = Field(min_length=1, max_length=120)
    recurring: bool = False


class HolidayUpdate(ApiModel):
    holiday_date: date | None = None
    name_el: str | None = Field(default=None, min_length=1, max_length=120)
    name_en: str | None = Field(default=None, min_length=1, max_length=120)
    recurring: bool | None = None


class HolidayImportIn(ApiModel):
    csv: str = Field(
        description="Columns: date (YYYY-MM-DD), name_el, name_en[, recurring]", max_length=200_000
    )


class RuleOut(ApiModel):
    id: UUID
    leave_type_id: UUID | None
    min_working_days: float
    max_working_days: float | None
    requires_document: bool
    steps: list[str]
    auto_acknowledge: bool
    priority: int
    active: bool

    @classmethod
    def from_domain(cls, r: ApprovalRule) -> RuleOut:
        return cls(
            id=r.id,
            leave_type_id=r.leave_type_id,
            min_working_days=days(r.min_working_days),
            max_working_days=days(r.max_working_days) if r.max_working_days is not None else None,
            requires_document=r.requires_document,
            steps=[s.value for s in r.steps],
            auto_acknowledge=r.auto_acknowledge,
            priority=r.priority,
            active=r.active,
        )


class RuleCreate(ApiModel):
    steps: list[StepRoleField]
    leave_type: str | None = Field(default=None, max_length=32)
    min_working_days: Decimal = Field(default=Decimal("0"), ge=0)
    max_working_days: Decimal | None = Field(default=None, ge=0)
    requires_document: bool = False
    auto_acknowledge: bool = False
    priority: int = Field(default=100, ge=0)


class RuleUpdate(ApiModel):
    steps: list[StepRoleField] | None = None
    leave_type: str | None = Field(default=None, max_length=32)
    min_working_days: Decimal | None = Field(default=None, ge=0)
    max_working_days: Decimal | None = Field(default=None, ge=0)
    requires_document: bool | None = None
    auto_acknowledge: bool | None = None
    priority: int | None = Field(default=None, ge=0)
    active: bool | None = None


class SimulateIn(ApiModel):
    leave_type: str = Field(max_length=32)
    start_date: date
    end_date: date
    employee_id: UUID


class AdjustmentIn(ApiModel):
    employee_id: UUID
    leave_type: str = Field(max_length=32)
    quantity_days: Decimal = Field(description="Signed; multiples of 0.5")
    reason: str = Field(min_length=1, max_length=500)
    period_year: int | None = Field(default=None, ge=2000, le=2100)
    effective_date: date | None = None


class TenantBalanceOut(ApiModel):
    employee_id: UUID
    leave_type_id: UUID
    period_year: int
    balance_days: float
    pending_days: float
    available_days: float

    @classmethod
    def from_row(cls, r: BalanceRow) -> TenantBalanceOut:
        return cls(
            employee_id=r.employee_id,
            leave_type_id=r.leave_type_id,
            period_year=r.period_year,
            balance_days=days(r.balance_days),
            pending_days=days(r.pending_days),
            available_days=days(r.balance_days - r.pending_days),
        )
