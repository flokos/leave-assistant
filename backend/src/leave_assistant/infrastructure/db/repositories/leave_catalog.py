"""SQL repositories for the leave catalogue (types, policies, holidays, rules). Tenant-scoped on top of RLS."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.domain.leave.types import (
    ApprovalRule,
    GrantMode,
    LeavePolicy,
    LeaveType,
    PublicHoliday,
    StepRole,
)
from leave_assistant.infrastructure.db.models import (
    ApprovalRuleRow,
    LeavePolicyRow,
    LeaveTypeRow,
    PublicHolidayRow,
)
from leave_assistant.infrastructure.db.session import flush_or_conflict


class _Scoped:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id


def _copy(
    row: Any, obj: Any, fields: tuple[str, ...], convert: Callable[[str, Any], Any] | None = None
) -> None:
    for field in fields:
        value = getattr(obj, field)
        setattr(row, field, convert(field, value) if convert else value)


class SqlLeaveTypeRepository(_Scoped):
    FIELDS = (
        "code",
        "name_el",
        "name_en",
        "balance_bearing",
        "allow_retroactive_days",
        "requires_document_above_days",
        "active",
        "created_at",
        "updated_at",
    )

    def _base(self) -> Select[LeaveTypeRow]:
        return select(LeaveTypeRow).where(LeaveTypeRow.tenant_id == self._tenant)

    @staticmethod
    def _to_domain(r: LeaveTypeRow) -> LeaveType:
        return LeaveType(
            r.id,
            r.tenant_id,
            r.code,
            r.name_el,
            r.name_en,
            r.balance_bearing,
            r.allow_retroactive_days,
            r.requires_document_above_days,
            r.active,
            r.created_at,
            r.updated_at,
        )

    async def get(self, leave_type_id: UUID) -> LeaveType | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(LeaveTypeRow.id == leave_type_id))
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_code(self, code: str) -> LeaveType | None:
        if self._tenant is None:
            return None
        row = (await self._s.execute(self._base().where(LeaveTypeRow.code == code))).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self, *, include_inactive: bool = False) -> builtins.list[LeaveType]:
        if self._tenant is None:
            return []
        stmt = self._base().order_by(LeaveTypeRow.code)
        if not include_inactive:
            stmt = stmt.where(LeaveTypeRow.active.is_(True))
        return [self._to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, leave_type: LeaveType) -> None:
        row = LeaveTypeRow(id=leave_type.id, tenant_id=leave_type.tenant_id)
        _copy(row, leave_type, self.FIELDS)
        self._s.add(row)
        await flush_or_conflict(self._s)

    async def update(self, leave_type: LeaveType) -> None:
        row = (await self._s.execute(self._base().where(LeaveTypeRow.id == leave_type.id))).scalar_one()
        _copy(row, leave_type, self.FIELDS)
        await flush_or_conflict(self._s)


class SqlLeavePolicyRepository(_Scoped):
    FIELDS = (
        "leave_type_id",
        "year",
        "entitlement_days",
        "carryover_max_days",
        "carryover_expires_on",
        "allow_negative",
        "min_notice_days",
        "max_consecutive_days",
        "prorate_on_hire",
        "created_at",
        "updated_at",
    )

    def _base(self) -> Select[LeavePolicyRow]:
        return select(LeavePolicyRow).where(LeavePolicyRow.tenant_id == self._tenant)

    @staticmethod
    def _to_domain(r: LeavePolicyRow) -> LeavePolicy:
        return LeavePolicy(
            r.id,
            r.tenant_id,
            r.leave_type_id,
            r.year,
            r.entitlement_days,
            r.carryover_max_days,
            r.carryover_expires_on,
            r.allow_negative,
            r.min_notice_days,
            r.max_consecutive_days,
            r.prorate_on_hire,
            GrantMode(r.grant_mode),
            r.created_at,
            r.updated_at,
        )

    async def get(self, policy_id: UUID) -> LeavePolicy | None:
        if self._tenant is None:
            return None
        row = (await self._s.execute(self._base().where(LeavePolicyRow.id == policy_id))).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_for(self, leave_type_id: UUID, year: int) -> LeavePolicy | None:
        if self._tenant is None:
            return None
        stmt = self._base().where(LeavePolicyRow.leave_type_id == leave_type_id, LeavePolicyRow.year == year)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self, *, year: int | None = None) -> builtins.list[LeavePolicy]:
        if self._tenant is None:
            return []
        stmt = self._base().order_by(LeavePolicyRow.year, LeavePolicyRow.leave_type_id)
        if year is not None:
            stmt = stmt.where(LeavePolicyRow.year == year)
        return [self._to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, policy: LeavePolicy) -> None:
        row = LeavePolicyRow(id=policy.id, tenant_id=policy.tenant_id, grant_mode=policy.grant_mode.value)
        _copy(row, policy, self.FIELDS)
        self._s.add(row)
        await flush_or_conflict(self._s)

    async def update(self, policy: LeavePolicy) -> None:
        row = (await self._s.execute(self._base().where(LeavePolicyRow.id == policy.id))).scalar_one()
        _copy(row, policy, self.FIELDS)
        row.grant_mode = policy.grant_mode.value
        await flush_or_conflict(self._s)

    async def delete(self, policy_id: UUID) -> None:
        await self._s.execute(
            delete(LeavePolicyRow).where(
                LeavePolicyRow.id == policy_id, LeavePolicyRow.tenant_id == self._tenant
            )
        )


class SqlHolidayRepository(_Scoped):
    FIELDS = ("holiday_date", "name_el", "name_en", "recurring", "created_at", "updated_at")

    def _base(self) -> Select[PublicHolidayRow]:
        return select(PublicHolidayRow).where(PublicHolidayRow.tenant_id == self._tenant)

    @staticmethod
    def _to_domain(r: PublicHolidayRow) -> PublicHoliday:
        return PublicHoliday(
            r.id, r.tenant_id, r.holiday_date, r.name_el, r.name_en, r.recurring, r.created_at, r.updated_at
        )

    async def get(self, holiday_id: UUID) -> PublicHoliday | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(PublicHolidayRow.id == holiday_id))
        ).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self, *, year: int | None = None) -> builtins.list[PublicHoliday]:
        if self._tenant is None:
            return []
        rows = (await self._s.execute(self._base().order_by(PublicHolidayRow.holiday_date))).scalars()
        return [
            self._to_domain(r) for r in rows if year is None or r.holiday_date.year == year or r.recurring
        ]

    async def add(self, holiday: PublicHoliday) -> None:
        row = PublicHolidayRow(id=holiday.id, tenant_id=holiday.tenant_id)
        _copy(row, holiday, self.FIELDS)
        self._s.add(row)
        await flush_or_conflict(self._s)

    async def update(self, holiday: PublicHoliday) -> None:
        row = (await self._s.execute(self._base().where(PublicHolidayRow.id == holiday.id))).scalar_one()
        _copy(row, holiday, self.FIELDS)
        await flush_or_conflict(self._s)

    async def delete(self, holiday_id: UUID) -> None:
        await self._s.execute(
            delete(PublicHolidayRow).where(
                PublicHolidayRow.id == holiday_id, PublicHolidayRow.tenant_id == self._tenant
            )
        )


class SqlApprovalRuleRepository(_Scoped):
    FIELDS = (
        "leave_type_id",
        "min_working_days",
        "max_working_days",
        "requires_document",
        "auto_acknowledge",
        "priority",
        "active",
        "created_at",
        "updated_at",
    )

    def _base(self) -> Select[ApprovalRuleRow]:
        return select(ApprovalRuleRow).where(ApprovalRuleRow.tenant_id == self._tenant)

    @staticmethod
    def _to_domain(r: ApprovalRuleRow) -> ApprovalRule:
        return ApprovalRule(
            r.id,
            r.tenant_id,
            r.leave_type_id,
            r.min_working_days,
            r.max_working_days,
            r.requires_document,
            tuple(StepRole(s) for s in r.steps),
            r.auto_acknowledge,
            r.priority,
            r.active,
            r.created_at,
            r.updated_at,
        )

    async def get(self, rule_id: UUID) -> ApprovalRule | None:
        if self._tenant is None:
            return None
        row = (await self._s.execute(self._base().where(ApprovalRuleRow.id == rule_id))).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self, *, include_inactive: bool = False) -> builtins.list[ApprovalRule]:
        if self._tenant is None:
            return []
        stmt = self._base().order_by(ApprovalRuleRow.priority, ApprovalRuleRow.id)
        if not include_inactive:
            stmt = stmt.where(ApprovalRuleRow.active.is_(True))
        return [self._to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, rule: ApprovalRule) -> None:
        row = ApprovalRuleRow(id=rule.id, tenant_id=rule.tenant_id, steps=[s.value for s in rule.steps])
        _copy(row, rule, self.FIELDS)
        self._s.add(row)
        await flush_or_conflict(self._s)

    async def update(self, rule: ApprovalRule) -> None:
        row = (await self._s.execute(self._base().where(ApprovalRuleRow.id == rule.id))).scalar_one()
        _copy(row, rule, self.FIELDS)
        row.steps = [s.value for s in rule.steps]
        await flush_or_conflict(self._s)

    async def delete(self, rule_id: UUID) -> None:
        await self._s.execute(
            delete(ApprovalRuleRow).where(
                ApprovalRuleRow.id == rule_id, ApprovalRuleRow.tenant_id == self._tenant
            )
        )
