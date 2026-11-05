"""Ledger service (ADR-0005): balances, own ledger, audited HR adjustments, grants / carry-over / expiry postings."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, ResourceRef, require
from leave_assistant.application.errors import InvalidInputError
from leave_assistant.application.leave.commands import Adjustment
from leave_assistant.application.leave.common import load_employee, load_leave_type, tenant_user
from leave_assistant.application.leave.views import BalanceView
from leave_assistant.application.ports import BalanceRow, Clock, IdGenerator, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.ledger import (
    EntryType,
    LedgerEntry,
    adjustment_key,
    carryover_amount,
    carryover_key,
    expiry_key,
    grant_key,
    prorated_entitlement,
)
from leave_assistant.domain.leave.types import LeavePolicy, LeaveType
from leave_assistant.domain.tenancy import Employee, Principal

ZERO = Decimal("0")


class LedgerService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, ids: IdGenerator, clock: Clock, audit: AuditService
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit

    # -- postings (inside the caller's transaction) --------------------------------------------------------
    async def post(
        self,
        uow: UnitOfWork,
        *,
        employee_id: UUID,
        leave_type_id: UUID,
        period_year: int,
        entry_type: EntryType,
        quantity_days: Decimal,
        effective_date: date,
        key: str,
        request_id: UUID | None = None,
        reason: str | None = None,
        created_by_employee_id: UUID | None = None,
    ) -> LedgerEntry | None:
        """Idempotent: an existing key means the posting already happened (re-runs, retries)."""
        if quantity_days == ZERO or await uow.ledger.exists(key):
            return None
        tenant_id = uow.context.tenant_id if uow.context else None
        if tenant_id is None:
            raise RuntimeError("ledger postings need a tenant context")
        entry = LedgerEntry(
            id=self._ids.new_id(),
            tenant_id=tenant_id,
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            period_year=period_year,
            entry_type=entry_type,
            quantity_days=quantity_days,
            effective_date=effective_date,
            idempotency_key=key,
            created_at=self._clock.now(),
            request_id=request_id,
            reason=reason,
            created_by_employee_id=created_by_employee_id,
        )
        await uow.ledger.add(entry)
        return entry

    async def grant_year(
        self, uow: UnitOfWork, *, year: int, employees: Iterable[Employee], policies: dict[UUID, LeavePolicy]
    ) -> int:
        """Up-front grants for `year` (pro-rated for hires during the year). Idempotent per employee/type/year."""
        posted = 0
        for employee in employees:
            if not employee.is_active:
                continue
            for leave_type_id, policy in policies.items():
                amount = prorated_entitlement(
                    policy.entitlement_days,
                    hire_date=employee.hire_date,
                    year=year,
                    prorate=policy.prorate_on_hire,
                )
                entry = await self.post(
                    uow,
                    employee_id=employee.id,
                    leave_type_id=leave_type_id,
                    period_year=year,
                    entry_type=EntryType.GRANT,
                    quantity_days=amount,
                    effective_date=max(date(year, 1, 1), employee.hire_date),
                    key=grant_key(employee.id, leave_type_id, year),
                    reason=f"entitlement {year}",
                )
                posted += entry is not None
        return posted

    async def carry_over(
        self, uow: UnitOfWork, *, year: int, employees: Iterable[Employee], policies: dict[UUID, LeavePolicy]
    ) -> int:
        """`CARRYOVER` into `year` = min(previous-year balance, cap) per employee/type (ADR-0028)."""
        posted = 0
        employees = list(employees)
        if not employees:
            return 0
        previous = {(r.employee_id, r.leave_type_id): r for r in await uow.ledger.balances(year=year - 1)}
        for employee in employees:
            for leave_type_id, policy in policies.items():
                if policy.carryover_max_days <= ZERO:
                    continue
                row = previous.get((employee.id, leave_type_id))
                amount = carryover_amount(row.balance_days if row else ZERO, cap=policy.carryover_max_days)
                entry = await self.post(
                    uow,
                    employee_id=employee.id,
                    leave_type_id=leave_type_id,
                    period_year=year,
                    entry_type=EntryType.CARRYOVER,
                    quantity_days=amount,
                    effective_date=date(year, 1, 1),
                    key=carryover_key(employee.id, leave_type_id, year),
                    reason=f"carry-over from {year - 1}",
                )
                posted += entry is not None
        return posted

    async def expire(
        self,
        uow: UnitOfWork,
        *,
        year: int,
        today: date,
        employees: Iterable[Employee],
        policies: dict[UUID, LeavePolicy],
    ) -> int:
        """`EXPIRY` of unused carry-over on the policy's expiry date (carried-over days are consumed first)."""
        posted = 0
        for employee in employees:
            for leave_type_id, policy in policies.items():
                expires = policy.carryover_expires_on
                if expires is None or today < expires:
                    continue
                entries = await uow.ledger.list(
                    employee_id=employee.id, leave_type_id=leave_type_id, year=year
                )
                carried = sum((e.quantity_days for e in entries if e.entry_type is EntryType.CARRYOVER), ZERO)
                if carried <= ZERO:
                    continue
                used = -sum(
                    (
                        e.quantity_days
                        for e in entries
                        if e.entry_type is EntryType.DEDUCTION and e.effective_date < expires
                    ),
                    ZERO,
                )
                reversed_ = sum(
                    (
                        e.quantity_days
                        for e in entries
                        if e.entry_type is EntryType.REVERSAL and e.effective_date < expires
                    ),
                    ZERO,
                )
                remaining = carried - max(ZERO, used - reversed_)
                if remaining <= ZERO:
                    continue
                entry = await self.post(
                    uow,
                    employee_id=employee.id,
                    leave_type_id=leave_type_id,
                    period_year=year,
                    entry_type=EntryType.EXPIRY,
                    quantity_days=-remaining,
                    effective_date=expires,
                    key=expiry_key(employee.id, leave_type_id, year),
                    reason="unused carry-over expired",
                )
                posted += entry is not None
        return posted

    # -- reads ----------------------------------------------------------------------------------------------
    async def balances(self, principal: Principal, *, year: int) -> list[BalanceView]:
        tenant_id, employee_id = tenant_user(principal)
        require(
            principal,
            Action.READ_OWN_LEAVE,
            ResourceRef("balance", tenant_id=tenant_id, owner_employee_id=employee_id),
        )
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            types = {t.id: t for t in await uow.leave_types.list()}
            rows = await uow.ledger.balances(year=year, employee_id=employee_id)
        return _balance_views(types, rows)

    async def ledger(
        self, principal: Principal, *, leave_type_code: str | None, year: int | None
    ) -> list[LedgerEntry]:
        tenant_id, employee_id = tenant_user(principal)
        require(
            principal,
            Action.READ_OWN_LEAVE,
            ResourceRef("ledger", tenant_id=tenant_id, owner_employee_id=employee_id),
        )
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            leave_type_id = (await load_leave_type(uow, leave_type_code)).id if leave_type_code else None
            return await uow.ledger.list(employee_id=employee_id, leave_type_id=leave_type_id, year=year)

    async def tenant_balances(self, principal: Principal, *, year: int) -> list[BalanceRow]:
        require(principal, Action.READ_TENANT_BALANCES)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.ledger.balances(year=year)

    async def adjust(self, principal: Principal, cmd: Adjustment) -> LedgerEntry:
        require(principal, Action.ADJUST_LEDGER)
        if cmd.quantity_days == ZERO:
            raise InvalidInputError(
                "quantity_days must not be zero", errors=[{"loc": ["body", "quantity_days"], "msg": "zero"}]
            )
        if not cmd.reason.strip():
            raise InvalidInputError(
                "a reason is required", errors=[{"loc": ["body", "reason"], "msg": "required"}]
            )
        today = self._clock.now().date()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            employee = await load_employee(uow, cmd.employee_id)
            leave_type = await load_leave_type(uow, cmd.leave_type_code)
            if not leave_type.balance_bearing:
                raise InvalidInputError(
                    "this leave type has no balance",
                    errors=[{"loc": ["body", "leave_type"], "msg": "not balance-bearing"}],
                )
            await uow.leave_requests.lock_employee(employee.id)
            effective = cmd.effective_date or today
            entry = await self.post(
                uow,
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                period_year=cmd.period_year or effective.year,
                entry_type=EntryType.ADJUSTMENT,
                quantity_days=cmd.quantity_days,
                effective_date=effective,
                key=adjustment_key(self._ids.new_id()),
                reason=cmd.reason.strip(),
                created_by_employee_id=principal.employee_id,
            )
            assert entry is not None
            await self._audit.record(
                uow,
                principal,
                "ledger.entry.posted",
                resource_type="ledger_entry",
                resource_id=entry.id,
                details={
                    "entry_type": entry.entry_type,
                    "quantity_days": str(entry.quantity_days),
                    "employee_id": employee.id,
                    "leave_type": leave_type.code,
                    "period_year": entry.period_year,
                },
            )
            await uow.commit()
        return entry


def _balance_views(types: dict[UUID, LeaveType], rows: list[BalanceRow]) -> list[BalanceView]:
    views = []
    for row in rows:
        leave_type = types.get(row.leave_type_id)
        if leave_type is None:
            continue
        views.append(
            BalanceView(
                leave_type,
                row.period_year,
                row.balance_days,
                row.pending_days,
                row.balance_days - row.pending_days,
            )
        )
    return sorted(views, key=lambda v: v.leave_type.code)
