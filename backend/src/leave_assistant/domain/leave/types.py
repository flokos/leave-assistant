"""Leave catalogue entities: types, policies, public holidays, approval rules (doc 07 §4.3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class LeaveTypeCode(StrEnum):
    ANNUAL = "annual"
    SICK = "sick"
    UNPAID = "unpaid"


class GrantMode(StrEnum):
    UPFRONT = "upfront"
    MONTHLY_ACCRUAL = "monthly_accrual"  # supported by the ledger model; not implemented (ADR-0028)


class StepRole(StrEnum):
    MANAGER = "manager"
    HR = "hr"


@dataclass(slots=True)
class LeaveType:
    id: UUID
    tenant_id: UUID
    code: str
    name_el: str
    name_en: str
    balance_bearing: bool
    allow_retroactive_days: int
    requires_document_above_days: int | None
    active: bool
    created_at: datetime
    updated_at: datetime

    @property
    def is_sick(self) -> bool:
        return self.code == LeaveTypeCode.SICK.value


@dataclass(slots=True)
class LeavePolicy:
    id: UUID
    tenant_id: UUID
    leave_type_id: UUID
    year: int
    entitlement_days: Decimal
    carryover_max_days: Decimal
    carryover_expires_on: date | None
    allow_negative: bool
    min_notice_days: int
    max_consecutive_days: int | None
    prorate_on_hire: bool
    grant_mode: GrantMode
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class PublicHoliday:
    id: UUID
    tenant_id: UUID
    holiday_date: date
    name_el: str
    name_en: str
    recurring: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class ApprovalRule:
    id: UUID
    tenant_id: UUID
    leave_type_id: UUID | None  # None = any leave type
    min_working_days: Decimal
    max_working_days: Decimal | None
    requires_document: bool
    steps: tuple[StepRole, ...]
    auto_acknowledge: bool
    priority: int
    active: bool
    created_at: datetime
    updated_at: datetime

    def matches(self, leave_type_id: UUID, working_days: Decimal) -> bool:
        if not self.active:
            return False
        if self.leave_type_id is not None and self.leave_type_id != leave_type_id:
            return False
        if working_days < self.min_working_days:
            return False
        return self.max_working_days is None or working_days <= self.max_working_days
