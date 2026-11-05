"""Leave catalogue administration (doc 09 §2.8): types, policies, holidays, approval rules; simulation."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.errors import ConflictError, InvalidInputError, NotFoundError
from leave_assistant.application.leave.commands import (
    CreateHoliday,
    CreateLeaveType,
    CreatePolicy,
    CreateRule,
    SimulateRequest,
    UpdateHoliday,
    UpdateLeaveType,
    UpdatePolicy,
    UpdateRule,
)
from leave_assistant.application.leave.common import load_employee, load_leave_type, load_tenant
from leave_assistant.application.leave.planner import RoutingPlanner
from leave_assistant.application.ports import Clock, IdGenerator, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.errors import InvalidValue
from leave_assistant.domain.leave.types import ApprovalRule, LeavePolicy, LeaveType, PublicHoliday, StepRole
from leave_assistant.domain.leave.validation import ValidationReport
from leave_assistant.domain.tenancy import Principal

ZERO = Decimal("0")


class LeaveCatalogService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        planner: RoutingPlanner,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._planner = planner

    async def _changed(
        self, uow: UnitOfWork, principal: Principal, entity: str, entity_id: UUID, changes: dict[str, Any]
    ) -> None:
        await self._audit.record(
            uow,
            principal,
            "admin.config.changed",
            resource_type=entity,
            resource_id=entity_id,
            details={"changes": changes},
        )

    # -- leave types ----------------------------------------------------------------------------------------
    async def list_types(self, principal: Principal, *, include_inactive: bool = False) -> list[LeaveType]:
        require(principal, Action.READ_LEAVE_CATALOGUE)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.leave_types.list(include_inactive=include_inactive)

    async def create_type(self, principal: Principal, cmd: CreateLeaveType) -> LeaveType:
        require(principal, Action.MANAGE_ORGANISATION)
        code = cmd.code.strip().lower()
        if not code or len(code) > 32 or not all(c.isalnum() or c == "_" for c in code):
            raise InvalidValue("code must be 1–32 letters, digits or '_'", params={"field": "code"})
        if cmd.allow_retroactive_days < 0 or (
            cmd.requires_document_above_days is not None and cmd.requires_document_above_days < 0
        ):
            raise InvalidValue(
                "day thresholds must not be negative", params={"field": "allow_retroactive_days"}
            )
        now = self._clock.now()
        assert principal.tenant_id is not None
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            if await uow.leave_types.get_by_code(code) is not None:
                raise ConflictError(f"leave type {code!r} already exists")
            leave_type = LeaveType(
                id=self._ids.new_id(),
                tenant_id=principal.tenant_id,
                code=code,
                name_el=cmd.name_el.strip(),
                name_en=cmd.name_en.strip(),
                balance_bearing=cmd.balance_bearing,
                allow_retroactive_days=cmd.allow_retroactive_days,
                requires_document_above_days=cmd.requires_document_above_days,
                active=True,
                created_at=now,
                updated_at=now,
            )
            await uow.leave_types.add(leave_type)
            await self._changed(uow, principal, "leave_type", leave_type.id, {"created": code})
            await uow.commit()
        return leave_type

    async def update_type(self, principal: Principal, leave_type_id: UUID, cmd: UpdateLeaveType) -> LeaveType:
        require(principal, Action.MANAGE_ORGANISATION)
        changes: dict[str, Any] = {}
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            leave_type = await uow.leave_types.get(leave_type_id)
            if leave_type is None:
                raise NotFoundError("leave type not found")
            for field in ("name_el", "name_en", "balance_bearing", "allow_retroactive_days", "active"):
                value = getattr(cmd, field)
                if value is not None and value != getattr(leave_type, field):
                    setattr(leave_type, field, value.strip() if isinstance(value, str) else value)
                    changes[field] = value
            if (
                cmd.requires_document_above_days is not UNSET
                and cmd.requires_document_above_days != leave_type.requires_document_above_days
            ):
                leave_type.requires_document_above_days = cmd.requires_document_above_days
                changes["requires_document_above_days"] = cmd.requires_document_above_days
            if changes:
                leave_type.updated_at = self._clock.now()
                await uow.leave_types.update(leave_type)
                await self._changed(uow, principal, "leave_type", leave_type.id, changes)
                await uow.commit()
        return leave_type

    # -- policies -------------------------------------------------------------------------------------------
    async def list_policies(self, principal: Principal, *, year: int | None = None) -> list[LeavePolicy]:
        require(principal, Action.READ_LEAVE_CATALOGUE)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.leave_policies.list(year=year)

    async def create_policy(self, principal: Principal, cmd: CreatePolicy) -> LeavePolicy:
        require(principal, Action.MANAGE_ORGANISATION)
        _check_policy_values(
            cmd.entitlement_days, cmd.carryover_max_days, cmd.min_notice_days, cmd.max_consecutive_days
        )
        now = self._clock.now()
        assert principal.tenant_id is not None
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            leave_type = await load_leave_type(uow, cmd.leave_type_code)
            if await uow.leave_policies.get_for(leave_type.id, cmd.year) is not None:
                raise ConflictError(f"a policy for {leave_type.code!r} and {cmd.year} already exists")
            policy = LeavePolicy(
                id=self._ids.new_id(),
                tenant_id=principal.tenant_id,
                leave_type_id=leave_type.id,
                year=cmd.year,
                entitlement_days=cmd.entitlement_days,
                carryover_max_days=cmd.carryover_max_days,
                carryover_expires_on=cmd.carryover_expires_on,
                allow_negative=cmd.allow_negative,
                min_notice_days=cmd.min_notice_days,
                max_consecutive_days=cmd.max_consecutive_days,
                prorate_on_hire=cmd.prorate_on_hire,
                grant_mode=cmd.grant_mode,
                created_at=now,
                updated_at=now,
            )
            await uow.leave_policies.add(policy)
            await self._changed(
                uow, principal, "leave_policy", policy.id, {"created": f"{leave_type.code}/{cmd.year}"}
            )
            await uow.commit()
        return policy

    async def update_policy(self, principal: Principal, policy_id: UUID, cmd: UpdatePolicy) -> LeavePolicy:
        require(principal, Action.MANAGE_ORGANISATION)
        changes: dict[str, Any] = {}
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            policy = await uow.leave_policies.get(policy_id)
            if policy is None:
                raise NotFoundError("policy not found")
            for field in (
                "entitlement_days",
                "carryover_max_days",
                "allow_negative",
                "min_notice_days",
                "prorate_on_hire",
            ):
                value = getattr(cmd, field)
                if value is not None and value != getattr(policy, field):
                    setattr(policy, field, value)
                    changes[field] = str(value)
            for field in ("carryover_expires_on", "max_consecutive_days"):
                value = getattr(cmd, field)
                if value is not UNSET and value != getattr(policy, field):
                    setattr(policy, field, value)
                    changes[field] = str(value)
            _check_policy_values(
                policy.entitlement_days,
                policy.carryover_max_days,
                policy.min_notice_days,
                policy.max_consecutive_days,
            )
            if changes:
                policy.updated_at = self._clock.now()
                await uow.leave_policies.update(policy)
                await self._changed(uow, principal, "leave_policy", policy.id, changes)
                await uow.commit()
        return policy

    async def delete_policy(self, principal: Principal, policy_id: UUID) -> None:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            policy = await uow.leave_policies.get(policy_id)
            if policy is None:
                raise NotFoundError("policy not found")
            await uow.leave_policies.delete(policy.id)
            await self._changed(uow, principal, "leave_policy", policy.id, {"deleted": True})
            await uow.commit()

    # -- holidays -------------------------------------------------------------------------------------------
    async def list_holidays(self, principal: Principal, *, year: int | None = None) -> list[PublicHoliday]:
        require(principal, Action.READ_LEAVE_CATALOGUE)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.holidays.list(year=year)

    async def create_holiday(self, principal: Principal, cmd: CreateHoliday) -> PublicHoliday:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            holiday = await self._add_holiday(uow, principal, cmd)
            await self._changed(
                uow, principal, "public_holiday", holiday.id, {"created": holiday.holiday_date.isoformat()}
            )
            await uow.commit()
        return holiday

    async def _add_holiday(self, uow: UnitOfWork, principal: Principal, cmd: CreateHoliday) -> PublicHoliday:
        assert principal.tenant_id is not None
        if any(
            h.holiday_date == cmd.holiday_date for h in await uow.holidays.list(year=cmd.holiday_date.year)
        ):
            raise ConflictError(f"a holiday on {cmd.holiday_date.isoformat()} already exists")
        now = self._clock.now()
        holiday = PublicHoliday(
            id=self._ids.new_id(),
            tenant_id=principal.tenant_id,
            holiday_date=cmd.holiday_date,
            name_el=cmd.name_el.strip(),
            name_en=cmd.name_en.strip(),
            recurring=cmd.recurring,
            created_at=now,
            updated_at=now,
        )
        await uow.holidays.add(holiday)
        return holiday

    async def update_holiday(
        self, principal: Principal, holiday_id: UUID, cmd: UpdateHoliday
    ) -> PublicHoliday:
        require(principal, Action.MANAGE_ORGANISATION)
        changes: dict[str, Any] = {}
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            holiday = await uow.holidays.get(holiday_id)
            if holiday is None:
                raise NotFoundError("holiday not found")
            for field in ("holiday_date", "name_el", "name_en", "recurring"):
                value = getattr(cmd, field)
                if value is not None and value != getattr(holiday, field):
                    setattr(holiday, field, value.strip() if isinstance(value, str) else value)
                    changes[field] = str(value)
            if changes:
                holiday.updated_at = self._clock.now()
                await uow.holidays.update(holiday)
                await self._changed(uow, principal, "public_holiday", holiday.id, changes)
                await uow.commit()
        return holiday

    async def delete_holiday(self, principal: Principal, holiday_id: UUID) -> None:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            holiday = await uow.holidays.get(holiday_id)
            if holiday is None:
                raise NotFoundError("holiday not found")
            await uow.holidays.delete(holiday.id)
            await self._changed(
                uow, principal, "public_holiday", holiday.id, {"deleted": holiday.holiday_date.isoformat()}
            )
            await uow.commit()

    async def import_holidays_csv(self, principal: Principal, text: str) -> list[PublicHoliday]:
        """CSV columns: date (YYYY-MM-DD), name_el, name_en, recurring (true/false, optional). Existing dates are skipped."""
        require(principal, Action.MANAGE_ORGANISATION)
        reader = csv.DictReader(io.StringIO(text))
        required = {"date", "name_el", "name_en"}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise InvalidInputError("CSV header must contain: date, name_el, name_en[, recurring]")
        created: list[PublicHoliday] = []
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            existing = {h.holiday_date for h in await uow.holidays.list()}
            for line_no, row in enumerate(reader, start=2):
                try:
                    day = date.fromisoformat(row["date"].strip())
                except ValueError as exc:
                    raise InvalidInputError(f"line {line_no}: invalid date {row['date']!r}") from exc
                if day in existing:
                    continue
                recurring = (row.get("recurring") or "false").strip().lower() in {"1", "true", "yes"}
                holiday = await self._add_holiday(
                    uow, principal, CreateHoliday(day, row["name_el"], row["name_en"], recurring)
                )
                existing.add(day)
                created.append(holiday)
            if created:
                await self._changed(
                    uow, principal, "public_holiday", created[0].id, {"imported": len(created)}
                )
                await uow.commit()
        return created

    # -- approval rules -------------------------------------------------------------------------------------
    async def list_rules(self, principal: Principal, *, include_inactive: bool = False) -> list[ApprovalRule]:
        require(principal, Action.READ_LEAVE_CATALOGUE)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.approval_rules.list(include_inactive=include_inactive)

    async def create_rule(self, principal: Principal, cmd: CreateRule) -> ApprovalRule:
        require(principal, Action.MANAGE_ORGANISATION)
        _check_rule_values(cmd.min_working_days, cmd.max_working_days, cmd.steps, cmd.auto_acknowledge)
        now = self._clock.now()
        assert principal.tenant_id is not None
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            leave_type_id = (
                (await load_leave_type(uow, cmd.leave_type_code)).id if cmd.leave_type_code else None
            )
            rule = ApprovalRule(
                id=self._ids.new_id(),
                tenant_id=principal.tenant_id,
                leave_type_id=leave_type_id,
                min_working_days=cmd.min_working_days,
                max_working_days=cmd.max_working_days,
                requires_document=cmd.requires_document,
                steps=tuple(cmd.steps),
                auto_acknowledge=cmd.auto_acknowledge,
                priority=cmd.priority,
                active=True,
                created_at=now,
                updated_at=now,
            )
            await uow.approval_rules.add(rule)
            await self._changed(
                uow, principal, "approval_rule", rule.id, {"created": True, "priority": cmd.priority}
            )
            await uow.commit()
        return rule

    async def update_rule(self, principal: Principal, rule_id: UUID, cmd: UpdateRule) -> ApprovalRule:
        require(principal, Action.MANAGE_ORGANISATION)
        changes: dict[str, Any] = {}
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            rule = await uow.approval_rules.get(rule_id)
            if rule is None:
                raise NotFoundError("approval rule not found")
            if cmd.steps is not None and tuple(cmd.steps) != rule.steps:
                rule.steps = tuple(cmd.steps)
                changes["steps"] = [s.value for s in cmd.steps]
            for field in ("min_working_days", "requires_document", "auto_acknowledge", "priority", "active"):
                value = getattr(cmd, field)
                if value is not None and value != getattr(rule, field):
                    setattr(rule, field, value)
                    changes[field] = str(value)
            if cmd.max_working_days is not UNSET and cmd.max_working_days != rule.max_working_days:
                rule.max_working_days = cmd.max_working_days
                changes["max_working_days"] = str(cmd.max_working_days)
            if cmd.leave_type_code is not UNSET:
                leave_type_id = (
                    (await load_leave_type(uow, cmd.leave_type_code)).id if cmd.leave_type_code else None
                )
                if leave_type_id != rule.leave_type_id:
                    rule.leave_type_id = leave_type_id
                    changes["leave_type"] = cmd.leave_type_code
            if changes:
                _check_rule_values(
                    rule.min_working_days, rule.max_working_days, rule.steps, rule.auto_acknowledge
                )
                rule.updated_at = self._clock.now()
                await uow.approval_rules.update(rule)
                await self._changed(uow, principal, "approval_rule", rule.id, changes)
                await uow.commit()
        return rule

    async def delete_rule(self, principal: Principal, rule_id: UUID) -> None:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            rule = await uow.approval_rules.get(rule_id)
            if rule is None:
                raise NotFoundError("approval rule not found")
            await uow.approval_rules.delete(rule.id)
            await self._changed(uow, principal, "approval_rule", rule.id, {"deleted": True})
            await uow.commit()

    # -- simulation (same planner as submission) ------------------------------------------------------------
    async def simulate(self, principal: Principal, cmd: SimulateRequest) -> ValidationReport:
        require(principal, Action.MANAGE_ORGANISATION)
        assert principal.tenant_id is not None
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            tenant = await load_tenant(uow, principal.tenant_id)
            employee = await load_employee(uow, cmd.employee_id)
            leave_type = await load_leave_type(uow, cmd.leave_type_code)
            plan = await self._planner.plan(
                uow,
                tenant=tenant,
                employee=employee,
                leave_type=leave_type,
                start=cmd.start_date,
                end=cmd.end_date,
            )
        return plan.report


def _check_policy_values(
    entitlement: Decimal, carryover: Decimal, min_notice: int, max_consecutive: int | None
) -> None:
    if entitlement < ZERO or carryover < ZERO:
        raise InvalidValue("day quantities must not be negative", params={"field": "entitlement_days"})
    if min_notice < 0 or (max_consecutive is not None and max_consecutive < 1):
        raise InvalidValue(
            "min_notice_days must be ≥ 0 and max_consecutive_days ≥ 1", params={"field": "min_notice_days"}
        )


def _check_rule_values(
    min_days: Decimal, max_days: Decimal | None, steps: tuple[StepRole, ...], auto: bool
) -> None:
    if min_days < ZERO or (max_days is not None and max_days < min_days):
        raise InvalidValue("working-day range is invalid", params={"field": "max_working_days"})
    if not steps and not auto:
        raise InvalidValue(
            "a rule needs at least one step unless it auto-acknowledges", params={"field": "steps"}
        )
