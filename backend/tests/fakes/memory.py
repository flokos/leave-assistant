"""In-memory unit of work emulating the transactional and RLS behaviour of the real adapter.

- Each entered unit of work operates on a snapshot; `commit()` publishes it to the shared store (rollback = discard).
- Tenant-scoped repositories only see rows of the context's tenant; no tenant → nothing (fail closed like RLS).
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Self
from uuid import UUID

from leave_assistant.application.audit.entry import AuditEntry
from leave_assistant.application.errors import ConflictError
from leave_assistant.application.idempotency import IdempotencyRecord
from leave_assistant.application.leave.errors import VersionConflict
from leave_assistant.application.notifications.models import IntegrationEvent, Notification
from leave_assistant.application.ports import (
    ApprovalRuleRepository,
    ApprovalStepRepository,
    AuditFilter,
    AuditRepository,
    BalanceRow,
    DepartmentRepository,
    EmployeeFilter,
    EmployeeRepository,
    HolidayRepository,
    IdempotencyRepository,
    IntegrationEventRepository,
    LeavePolicyRepository,
    LeaveRequestRepository,
    LeaveTypeRepository,
    LedgerRepository,
    NotificationRepository,
    RequestEventRepository,
    RequestFilter,
    TenantRepository,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.ledger import LedgerEntry
from leave_assistant.domain.leave.request import ApprovalStep, LeaveRequest, RequestEventRecord, StepStatus
from leave_assistant.domain.leave.state_machine import PENDING_STATUSES, RequestStatus
from leave_assistant.domain.leave.types import ApprovalRule, LeavePolicy, LeaveType, PublicHoliday, StepRole
from leave_assistant.domain.tenancy import Department, Employee, Role, Tenant


@dataclass
class MemoryStore:
    tenants: dict[UUID, Tenant] = field(default_factory=dict)
    departments: dict[UUID, Department] = field(default_factory=dict)
    employees: dict[UUID, Employee] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)
    idempotency: dict[tuple[str, str, str], IdempotencyRecord] = field(default_factory=dict)
    leave_types: dict[UUID, LeaveType] = field(default_factory=dict)
    leave_policies: dict[UUID, LeavePolicy] = field(default_factory=dict)
    holidays: dict[UUID, PublicHoliday] = field(default_factory=dict)
    approval_rules: dict[UUID, ApprovalRule] = field(default_factory=dict)
    leave_requests: dict[UUID, LeaveRequest] = field(default_factory=dict)
    approval_steps: dict[UUID, ApprovalStep] = field(default_factory=dict)
    request_events: list[RequestEventRecord] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    notifications: dict[UUID, Notification] = field(default_factory=dict)
    integration_events: list[IntegrationEvent] = field(default_factory=list)
    request_counters: dict[tuple[UUID, int], int] = field(default_factory=dict)

    def tables(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def _after(key: tuple[datetime, UUID], after: tuple[datetime, UUID] | None) -> bool:
    return after is None or key > after


class _Tenants:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    async def get(self, tenant_id: UUID) -> Tenant | None:
        return copy.deepcopy(self._s.tenants.get(tenant_id))

    async def get_by_slug(self, slug: str) -> Tenant | None:
        return copy.deepcopy(next((t for t in self._s.tenants.values() if t.slug == slug), None))

    async def list(self, *, limit: int, after: tuple[datetime, UUID] | None) -> list[Tenant]:
        rows = sorted(self._s.tenants.values(), key=lambda t: (t.created_at, t.id))
        return [copy.deepcopy(t) for t in rows if _after((t.created_at, t.id), after)][:limit]

    async def add(self, tenant: Tenant) -> None:
        if any(t.slug == tenant.slug for t in self._s.tenants.values()):
            raise ConflictError("duplicate slug")
        self._s.tenants[tenant.id] = copy.deepcopy(tenant)

    async def update(self, tenant: Tenant) -> None:
        self._s.tenants[tenant.id] = copy.deepcopy(tenant)


class _Departments:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    def _rows(self) -> list[Department]:
        return [
            d
            for d in self._s.departments.values()
            if self._tenant is not None and d.tenant_id == self._tenant
        ]

    async def get(self, department_id: UUID) -> Department | None:
        return copy.deepcopy(next((d for d in self._rows() if d.id == department_id), None))

    async def get_by_code(self, code: str) -> Department | None:
        return copy.deepcopy(next((d for d in self._rows() if d.code == code), None))

    async def list(self) -> list[Department]:
        return [copy.deepcopy(d) for d in sorted(self._rows(), key=lambda d: d.code)]

    async def add(self, department: Department) -> None:
        if department.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        if any(d.code == department.code for d in self._rows()):
            raise ConflictError("duplicate code")
        self._s.departments[department.id] = copy.deepcopy(department)

    async def update(self, department: Department) -> None:
        if department.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        self._s.departments[department.id] = copy.deepcopy(department)

    async def delete(self, department_id: UUID) -> None:
        if any(d.id == department_id for d in self._rows()):
            del self._s.departments[department_id]


class _Employees:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    def _rows(self) -> list[Employee]:
        return [
            e for e in self._s.employees.values() if self._tenant is not None and e.tenant_id == self._tenant
        ]

    async def get(self, employee_id: UUID) -> Employee | None:
        return copy.deepcopy(next((e for e in self._rows() if e.id == employee_id), None))

    async def get_by_sub(self, keycloak_sub: str) -> Employee | None:
        return copy.deepcopy(next((e for e in self._rows() if e.keycloak_sub == keycloak_sub), None))

    async def get_by_email(self, email: str) -> Employee | None:
        return copy.deepcopy(next((e for e in self._rows() if e.email == email), None))

    async def list(
        self, *, filters: EmployeeFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[Employee]:
        rows = self._rows()
        if filters.department_id is not None:
            rows = [e for e in rows if e.department_id == filters.department_id]
        if filters.status is not None:
            rows = [e for e in rows if e.status.value == filters.status]
        if filters.query:
            q = filters.query.lower()
            rows = [
                e
                for e in rows
                if q in e.display_name.lower() or q in e.email or q in e.employee_number.lower()
            ]
        rows = sorted(rows, key=lambda e: (e.created_at, e.id))
        return [copy.deepcopy(e) for e in rows if _after((e.created_at, e.id), after)][:limit]

    async def count_in_department(self, department_id: UUID) -> int:
        return sum(1 for e in self._rows() if e.department_id == department_id)

    async def get_many(self, employee_ids: Sequence[UUID]) -> dict[UUID, Employee]:
        wanted = set(employee_ids)
        return {e.id: copy.deepcopy(e) for e in self._rows() if e.id in wanted}

    async def list_active(
        self, *, department_id: UUID | None = None, role: Role | None = None
    ) -> list[Employee]:
        rows = [e for e in self._rows() if e.is_active]
        if department_id is not None:
            rows = [e for e in rows if e.department_id == department_id]
        if role is not None:
            rows = [e for e in rows if role in e.roles]
        return [copy.deepcopy(e) for e in sorted(rows, key=lambda e: (e.created_at, e.id))]

    async def add(self, employee: Employee) -> None:
        if employee.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        if any(
            e.email == employee.email or e.employee_number == employee.employee_number for e in self._rows()
        ):
            raise ConflictError("duplicate employee")
        if any(e.keycloak_sub == employee.keycloak_sub for e in self._s.employees.values()):
            raise ConflictError("duplicate keycloak_sub")
        self._s.employees[employee.id] = copy.deepcopy(employee)

    async def update(self, employee: Employee) -> None:
        if employee.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        self._s.employees[employee.id] = copy.deepcopy(employee)

    async def set_roles(self, employee_id: UUID, roles: frozenset[Role]) -> None:
        row = next((e for e in self._rows() if e.id == employee_id), None)
        if row is not None:
            row.roles = frozenset(roles)


class _Audit:
    def __init__(self, store: MemoryStore, context: TenantContext | None) -> None:
        self._s = store
        self._ctx = context

    async def add(self, entry: AuditEntry) -> None:
        tenant = self._ctx.tenant_id if self._ctx else None
        if entry.tenant_id != tenant:
            raise ConflictError("RLS: audit WITH CHECK failed")
        self._s.audit.append(entry)

    async def list(
        self, *, filters: AuditFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[AuditEntry]:
        tenant = self._ctx.tenant_id if self._ctx else None
        platform_admin = bool(self._ctx and self._ctx.platform_admin)
        rows = [
            a
            for a in self._s.audit
            if (a.tenant_id is not None and a.tenant_id == tenant) or (a.tenant_id is None and platform_admin)
        ]
        if filters.platform_only:
            rows = [a for a in rows if a.tenant_id is None]
        if filters.action:
            rows = [a for a in rows if a.action == filters.action]
        if filters.occurred_from:
            rows = [a for a in rows if a.occurred_at >= filters.occurred_from]
        if filters.occurred_to:
            rows = [a for a in rows if a.occurred_at < filters.occurred_to]
        rows = sorted(rows, key=lambda a: (a.occurred_at, a.id), reverse=True)
        if after is not None:
            rows = [a for a in rows if (a.occurred_at, a.id) < after]
        return rows[:limit]


class _Idempotency:
    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    async def get(self, user_id: str, route: str, key: str) -> IdempotencyRecord | None:
        return self._s.idempotency.get((user_id, route, key))

    async def add(self, record: IdempotencyRecord) -> None:
        self._s.idempotency[(record.user_id, record.route, record.key)] = record

    async def purge_expired(self, now: datetime) -> int:
        expired = [k for k, r in self._s.idempotency.items() if r.expires_at <= now]
        for k in expired:
            del self._s.idempotency[k]
        return len(expired)


class _TenantTable[T]:
    """Base for tenant-scoped in-memory tables keyed by id (rows carry `tenant_id`)."""

    def __init__(self, rows: dict[UUID, T], tenant_id: UUID | None) -> None:
        self._rows_dict = rows
        self._tenant = tenant_id

    def _rows(self) -> list[T]:
        return [
            r for r in self._rows_dict.values() if self._tenant is not None and r.tenant_id == self._tenant
        ]

    def _check(self, row: T) -> None:
        if row.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")


class _LeaveTypes(_TenantTable[LeaveType]):
    async def get(self, leave_type_id: UUID) -> LeaveType | None:
        return copy.deepcopy(next((t for t in self._rows() if t.id == leave_type_id), None))

    async def get_by_code(self, code: str) -> LeaveType | None:
        return copy.deepcopy(next((t for t in self._rows() if t.code == code), None))

    async def list(self, *, include_inactive: bool = False) -> list[LeaveType]:
        rows = [t for t in self._rows() if include_inactive or t.active]
        return [copy.deepcopy(t) for t in sorted(rows, key=lambda t: t.code)]

    async def add(self, leave_type: LeaveType) -> None:
        self._check(leave_type)
        if any(t.code == leave_type.code for t in self._rows()):
            raise ConflictError("duplicate leave type code")
        self._rows_dict[leave_type.id] = copy.deepcopy(leave_type)

    async def update(self, leave_type: LeaveType) -> None:
        self._check(leave_type)
        self._rows_dict[leave_type.id] = copy.deepcopy(leave_type)


class _LeavePolicies(_TenantTable[LeavePolicy]):
    async def get(self, policy_id: UUID) -> LeavePolicy | None:
        return copy.deepcopy(next((p for p in self._rows() if p.id == policy_id), None))

    async def get_for(self, leave_type_id: UUID, year: int) -> LeavePolicy | None:
        return copy.deepcopy(
            next((p for p in self._rows() if p.leave_type_id == leave_type_id and p.year == year), None)
        )

    async def list(self, *, year: int | None = None) -> list[LeavePolicy]:
        rows = [p for p in self._rows() if year is None or p.year == year]
        return [copy.deepcopy(p) for p in sorted(rows, key=lambda p: (p.year, p.leave_type_id.int))]

    async def add(self, policy: LeavePolicy) -> None:
        self._check(policy)
        if any(p.leave_type_id == policy.leave_type_id and p.year == policy.year for p in self._rows()):
            raise ConflictError("duplicate policy")
        self._rows_dict[policy.id] = copy.deepcopy(policy)

    async def update(self, policy: LeavePolicy) -> None:
        self._check(policy)
        self._rows_dict[policy.id] = copy.deepcopy(policy)

    async def delete(self, policy_id: UUID) -> None:
        if any(p.id == policy_id for p in self._rows()):
            del self._rows_dict[policy_id]


class _Holidays(_TenantTable[PublicHoliday]):
    async def get(self, holiday_id: UUID) -> PublicHoliday | None:
        return copy.deepcopy(next((h for h in self._rows() if h.id == holiday_id), None))

    async def list(self, *, year: int | None = None) -> list[PublicHoliday]:
        rows = [h for h in self._rows() if year is None or h.holiday_date.year == year or h.recurring]
        return [copy.deepcopy(h) for h in sorted(rows, key=lambda h: h.holiday_date)]

    async def add(self, holiday: PublicHoliday) -> None:
        self._check(holiday)
        if any(h.holiday_date == holiday.holiday_date for h in self._rows()):
            raise ConflictError("duplicate holiday date")
        self._rows_dict[holiday.id] = copy.deepcopy(holiday)

    async def update(self, holiday: PublicHoliday) -> None:
        self._check(holiday)
        self._rows_dict[holiday.id] = copy.deepcopy(holiday)

    async def delete(self, holiday_id: UUID) -> None:
        if any(h.id == holiday_id for h in self._rows()):
            del self._rows_dict[holiday_id]


class _ApprovalRules(_TenantTable[ApprovalRule]):
    async def get(self, rule_id: UUID) -> ApprovalRule | None:
        return copy.deepcopy(next((r for r in self._rows() if r.id == rule_id), None))

    async def list(self, *, include_inactive: bool = False) -> list[ApprovalRule]:
        rows = [r for r in self._rows() if include_inactive or r.active]
        return [copy.deepcopy(r) for r in sorted(rows, key=lambda r: (r.priority, r.id.int))]

    async def add(self, rule: ApprovalRule) -> None:
        self._check(rule)
        self._rows_dict[rule.id] = copy.deepcopy(rule)

    async def update(self, rule: ApprovalRule) -> None:
        self._check(rule)
        self._rows_dict[rule.id] = copy.deepcopy(rule)

    async def delete(self, rule_id: UUID) -> None:
        if any(r.id == rule_id for r in self._rows()):
            del self._rows_dict[rule_id]


class _LeaveRequests(_TenantTable[LeaveRequest]):
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        super().__init__(store.leave_requests, tenant_id)
        self._s = store

    async def get(self, request_id: UUID) -> LeaveRequest | None:
        return copy.deepcopy(next((r for r in self._rows() if r.id == request_id), None))

    async def list(
        self, *, filters: RequestFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[LeaveRequest]:
        rows = self._rows()
        if filters.employee_id is not None:
            rows = [r for r in rows if r.employee_id == filters.employee_id]
        if filters.department_id is not None:
            members = {e.id for e in self._s.employees.values() if e.department_id == filters.department_id}
            rows = [r for r in rows if r.employee_id in members]
        if filters.statuses is not None:
            rows = [r for r in rows if r.status in filters.statuses]
        if filters.date_from is not None:
            rows = [r for r in rows if r.end_date >= filters.date_from]
        if filters.date_to is not None:
            rows = [r for r in rows if r.start_date <= filters.date_to]
        rows = sorted(rows, key=lambda r: (r.created_at, r.id))
        return [copy.deepcopy(r) for r in rows if _after((r.created_at, r.id), after)][:limit]

    async def list_for_employee(self, employee_id: UUID) -> list[LeaveRequest]:
        return [copy.deepcopy(r) for r in self._rows() if r.employee_id == employee_id]

    async def list_in_window(
        self, start: date, end: date, *, statuses: frozenset[RequestStatus]
    ) -> list[LeaveRequest]:
        return [
            copy.deepcopy(r)
            for r in self._rows()
            if r.status in statuses and r.start_date <= end and start <= r.end_date
        ]

    async def list_by_status(self, statuses: frozenset[RequestStatus]) -> list[LeaveRequest]:
        return [copy.deepcopy(r) for r in self._rows() if r.status in statuses]

    async def add(self, request: LeaveRequest) -> None:
        self._check(request)
        self._rows_dict[request.id] = copy.deepcopy(request)

    async def update(self, request: LeaveRequest, *, expected_version: int, bump: bool = True) -> None:
        self._check(request)
        current = self._rows_dict.get(request.id)
        if current is None or current.version != expected_version:
            raise VersionConflict("stale version")
        stored = copy.deepcopy(request)
        stored.version = expected_version + 1 if bump else expected_version
        request.version = stored.version
        self._rows_dict[request.id] = stored

    async def lock_employee(self, employee_id: UUID) -> None:
        return None

    async def next_request_number(self, year: int) -> str:
        assert self._tenant is not None
        seq = self._s.request_counters.get((self._tenant, year), 0) + 1
        self._s.request_counters[(self._tenant, year)] = seq
        return f"LR-{year}-{seq:06d}"


class _ApprovalSteps(_TenantTable[ApprovalStep]):
    async def get(self, step_id: UUID) -> ApprovalStep | None:
        return copy.deepcopy(next((s for s in self._rows() if s.id == step_id), None))

    async def list_for_request(self, request_id: UUID) -> list[ApprovalStep]:
        return [
            copy.deepcopy(s)
            for s in sorted((s for s in self._rows() if s.request_id == request_id), key=lambda s: s.step_no)
        ]

    async def list_pending(
        self, *, designated_employee_id: UUID | None = None, role: StepRole | None = None
    ) -> list[ApprovalStep]:
        rows = [s for s in self._rows() if s.status is StepStatus.PENDING]
        if designated_employee_id is not None:
            rows = [s for s in rows if s.designated_employee_id == designated_employee_id]
        if role is not None:
            rows = [s for s in rows if s.approver_role is role]
        return [copy.deepcopy(s) for s in sorted(rows, key=lambda s: (s.created_at, s.step_no))]

    async def add(self, step: ApprovalStep) -> None:
        self._check(step)
        self._rows_dict[step.id] = copy.deepcopy(step)

    async def update(self, step: ApprovalStep, *, expected_version: int) -> None:
        self._check(step)
        current = self._rows_dict.get(step.id)
        if current is None or current.version != expected_version:
            raise VersionConflict("stale step version")
        stored = copy.deepcopy(step)
        stored.version = expected_version + 1
        step.version = stored.version
        self._rows_dict[step.id] = stored


class _RequestEvents:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    async def add(self, event: RequestEventRecord) -> None:
        if event.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        self._s.request_events.append(event)

    async def list_for_request(self, request_id: UUID) -> list[RequestEventRecord]:
        return [
            e for e in self._s.request_events if e.tenant_id == self._tenant and e.request_id == request_id
        ]


class _Ledger:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    def _rows(self) -> list[LedgerEntry]:
        return [e for e in self._s.ledger if self._tenant is not None and e.tenant_id == self._tenant]

    async def add(self, entry: LedgerEntry) -> None:
        if entry.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        if any(e.idempotency_key == entry.idempotency_key for e in self._s.ledger):
            raise ConflictError("duplicate ledger idempotency key")
        self._s.ledger.append(entry)

    async def exists(self, idempotency_key: str) -> bool:
        return any(e.idempotency_key == idempotency_key for e in self._rows())

    async def list(
        self, *, employee_id: UUID, leave_type_id: UUID | None = None, year: int | None = None
    ) -> list[LedgerEntry]:
        rows = [e for e in self._rows() if e.employee_id == employee_id]
        if leave_type_id is not None:
            rows = [e for e in rows if e.leave_type_id == leave_type_id]
        if year is not None:
            rows = [e for e in rows if e.period_year == year]
        return sorted(rows, key=lambda e: (e.effective_date, e.created_at))

    async def balances(self, *, year: int, employee_id: UUID | None = None) -> list[BalanceRow]:
        totals: dict[tuple[UUID, UUID], Decimal] = {}
        for e in self._rows():
            if e.period_year == year and (employee_id is None or e.employee_id == employee_id):
                totals[(e.employee_id, e.leave_type_id)] = (
                    totals.get((e.employee_id, e.leave_type_id), Decimal("0")) + e.quantity_days
                )
        pending: dict[tuple[UUID, UUID], Decimal] = {}
        for r in self._s.leave_requests.values():
            if (
                r.tenant_id == self._tenant
                and r.status in PENDING_STATUSES
                and r.start_date.year == year
                and (employee_id is None or r.employee_id == employee_id)
            ):
                pending[(r.employee_id, r.leave_type_id)] = (
                    pending.get((r.employee_id, r.leave_type_id), Decimal("0")) + r.working_days
                )
        keys = set(totals) | set(pending)
        return sorted(
            (
                BalanceRow(e, t, year, totals.get((e, t), Decimal("0")), pending.get((e, t), Decimal("0")))
                for e, t in keys
            ),
            key=lambda b: (b.employee_id.int, b.leave_type_id.int),
        )


class _Notifications(_TenantTable[Notification]):
    async def add(self, notification: Notification) -> None:
        self._check(notification)
        self._rows_dict[notification.id] = copy.deepcopy(notification)

    async def get(self, notification_id: UUID) -> Notification | None:
        return copy.deepcopy(next((n for n in self._rows() if n.id == notification_id), None))

    async def list(
        self,
        *,
        recipient_employee_id: UUID,
        unread_only: bool,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[Notification]:
        rows = [
            n
            for n in self._rows()
            if n.recipient_employee_id == recipient_employee_id and (not unread_only or n.read_at is None)
        ]
        rows = sorted(rows, key=lambda n: (n.created_at, n.id), reverse=True)
        if after is not None:
            rows = [n for n in rows if (n.created_at, n.id) < after]
        return [copy.deepcopy(n) for n in rows[:limit]]

    async def update(self, notification: Notification) -> None:
        self._check(notification)
        self._rows_dict[notification.id] = copy.deepcopy(notification)

    async def mark_all_read(self, recipient_employee_id: UUID, read_at: datetime) -> int:
        count = 0
        for n in self._rows():
            if n.recipient_employee_id == recipient_employee_id and n.read_at is None:
                n.read_at = read_at
                count += 1
        return count


class _IntegrationEvents:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    async def add(self, event: IntegrationEvent) -> None:
        if event.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        self._s.integration_events.append(event)


class MemoryUnitOfWork:
    leave_types: LeaveTypeRepository
    leave_policies: LeavePolicyRepository
    holidays: HolidayRepository
    approval_rules: ApprovalRuleRepository
    leave_requests: LeaveRequestRepository
    approval_steps: ApprovalStepRepository
    request_events: RequestEventRepository
    ledger: LedgerRepository
    notifications: NotificationRepository
    integration_events: IntegrationEventRepository
    tenants: TenantRepository
    departments: DepartmentRepository
    employees: EmployeeRepository
    audit: AuditRepository
    idempotency: IdempotencyRepository

    def __init__(self, store: MemoryStore, context: TenantContext | None) -> None:
        self._shared = store
        self.context = context
        self._committed = False

    async def __aenter__(self) -> Self:
        self._work = copy.deepcopy(self._shared)
        self._committed = False
        tenant = self.context.tenant_id if self.context else None
        self.tenants = _Tenants(self._work)
        self.departments = _Departments(self._work, tenant)
        self.employees = _Employees(self._work, tenant)
        self.audit = _Audit(self._work, self.context)
        self.idempotency = _Idempotency(self._work)
        self.leave_types = _LeaveTypes(self._work.leave_types, tenant)
        self.leave_policies = _LeavePolicies(self._work.leave_policies, tenant)
        self.holidays = _Holidays(self._work.holidays, tenant)
        self.approval_rules = _ApprovalRules(self._work.approval_rules, tenant)
        self.leave_requests = _LeaveRequests(self._work, tenant)
        self.approval_steps = _ApprovalSteps(self._work.approval_steps, tenant)
        self.request_events = _RequestEvents(self._work, tenant)
        self.ledger = _Ledger(self._work, tenant)
        self.notifications = _Notifications(self._work.notifications, tenant)
        self.integration_events = _IntegrationEvents(self._work, tenant)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def commit(self) -> None:
        for name in self._shared.__dataclass_fields__:
            setattr(self._shared, name, getattr(self._work, name))
        self._committed = True

    async def rollback(self) -> None:
        self._work = copy.deepcopy(self._shared)


class MemoryUnitOfWorkFactory:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or MemoryStore()

    def __call__(self, context: TenantContext | None) -> MemoryUnitOfWork:
        return MemoryUnitOfWork(self.store, context)
