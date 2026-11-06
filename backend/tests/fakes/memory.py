"""In-memory unit of work emulating the transactional and RLS behaviour of the real adapter.

- Each entered unit of work operates on a snapshot; `commit()` publishes it to the shared store (rollback = discard).
- Tenant-scoped repositories only see rows of the context's tenant; no tenant → nothing (fail closed like RLS).
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Self
from uuid import UUID

from leave_assistant.application.assistant.models import ActionProposal, PendingInterrupt
from leave_assistant.application.audit.entry import AuditEntry
from leave_assistant.application.chat.models import (
    AgentRun,
    Conversation,
    ConversationMessage,
    ConversationStatus,
    ProviderEventRecord,
)
from leave_assistant.application.errors import ConflictError
from leave_assistant.application.idempotency import IdempotencyRecord
from leave_assistant.application.jobs.models import Job, JobStatus
from leave_assistant.application.leave.errors import VersionConflict
from leave_assistant.application.notifications.models import IntegrationEvent, Notification
from leave_assistant.application.ports import (
    AclRepository,
    AgentRunRepository,
    ApprovalRuleRepository,
    ApprovalStepRepository,
    AuditFilter,
    AuditRepository,
    BalanceRow,
    ChunkRepository,
    ConversationRepository,
    DepartmentRepository,
    DocumentFilter,
    DocumentRepository,
    DocumentVersionRepository,
    DownloadToken,
    DownloadTokenRepository,
    EmployeeFilter,
    EmployeeRepository,
    HolidayRepository,
    IdempotencyRepository,
    IndexGenerationRepository,
    InjectionFinding,
    InjectionFindingRepository,
    IntegrationEventRepository,
    JobFilter,
    JobRepository,
    LeavePolicyRepository,
    LeaveRequestRepository,
    LeaveTypeRepository,
    LedgerRepository,
    MessageRepository,
    NotificationRepository,
    PendingInterruptRepository,
    ProposalRepository,
    ProviderEventRepository,
    RequestEventRepository,
    RequestFilter,
    RetentionPolicy,
    RetentionPolicyRepository,
    RetentionReviewItem,
    RetentionReviewItemRepository,
    ReviewEventRepository,
    SecurityDefaults,
    SecurityDefaultsRepository,
    SecurityPolicy,
    SecurityPolicyRepository,
    SupportingAccessEvent,
    SupportingAccessRepository,
    SupportingDocument,
    SupportingDocumentRepository,
    TenantRepository,
    VaultEntry,
    VaultRepository,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.agent.proposals import ProposalStatus
from leave_assistant.domain.documents.fusion import LegHit
from leave_assistant.domain.documents.types import (
    AclEntry,
    Chunk,
    Document,
    DocumentVersion,
    IndexGeneration,
    ReviewEvent,
    VersionStatus,
)
from leave_assistant.domain.documents.visibility import DocumentScope, Reader, can_read
from leave_assistant.domain.leave.ledger import LedgerEntry
from leave_assistant.domain.leave.request import ApprovalStep, LeaveRequest, RequestEventRecord, StepStatus
from leave_assistant.domain.leave.state_machine import PENDING_STATUSES, RequestStatus
from leave_assistant.domain.leave.types import ApprovalRule, LeavePolicy, LeaveType, PublicHoliday, StepRole
from leave_assistant.domain.privacy.types import EntityType
from leave_assistant.domain.retention.eligibility import DocumentClass, ReviewStatus
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
    documents: dict[UUID, Document] = field(default_factory=dict)
    document_versions: dict[UUID, DocumentVersion] = field(default_factory=dict)
    chunks: dict[UUID, Chunk] = field(default_factory=dict)
    document_acl: dict[UUID, AclEntry] = field(default_factory=dict)
    review_events: list[ReviewEvent] = field(default_factory=list)
    index_generations: dict[tuple[UUID, str], IndexGeneration] = field(default_factory=dict)
    jobs: dict[UUID, Job] = field(default_factory=dict)
    pii_vault: dict[UUID, VaultEntry] = field(default_factory=dict)
    pii_counters: dict[tuple[UUID, str], int] = field(default_factory=dict)
    security_defaults: SecurityDefaults = field(default_factory=SecurityDefaults)
    security_policies: dict[UUID, SecurityPolicy] = field(default_factory=dict)
    injection_findings: list[InjectionFinding] = field(default_factory=list)
    supporting_documents: dict[UUID, SupportingDocument] = field(default_factory=dict)
    supporting_access: list[SupportingAccessEvent] = field(default_factory=list)
    download_tokens: dict[str, DownloadToken] = field(default_factory=dict)
    retention_policies: dict[UUID, RetentionPolicy] = field(default_factory=dict)
    retention_items: dict[UUID, RetentionReviewItem] = field(default_factory=dict)
    conversations: dict[UUID, Conversation] = field(default_factory=dict)
    messages: list[ConversationMessage] = field(default_factory=list)
    agent_runs: list[AgentRun] = field(default_factory=list)
    provider_events: list[ProviderEventRecord] = field(default_factory=list)
    proposals: dict[UUID, ActionProposal] = field(default_factory=dict)
    pending_interrupts: dict[UUID, PendingInterrupt] = field(default_factory=dict)

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

    async def get_by_number(self, request_number: str) -> LeaveRequest | None:
        return copy.deepcopy(next((r for r in self._rows() if r.request_number == request_number), None))

    async def find_by_idempotency_key(self, employee_id: UUID, key: str) -> LeaveRequest | None:
        return copy.deepcopy(
            next((r for r in self._rows() if r.employee_id == employee_id and r.idempotency_key == key), None)
        )


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

    async def find_by_proposal(self, proposal_id: UUID) -> RequestEventRecord | None:
        return next(
            (
                e
                for e in self._s.request_events
                if e.tenant_id == self._tenant and e.payload.get("proposal_id") == str(proposal_id)
            ),
            None,
        )


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


# --- documents, chunks, jobs (M4) ------------------------------------------------------------------------------
_QUERY_WORD = re.compile(r"[\w']+", re.UNICODE)


def _scope(store: MemoryStore, document: Document) -> DocumentScope:
    manager_id = None
    if document.department_id is not None:
        department = store.departments.get(document.department_id)
        manager_id = department.manager_employee_id if department else None
    return DocumentScope(
        category=document.category,
        owner_employee_id=document.owner_employee_id,
        department_id=document.department_id,
        department_manager_employee_id=manager_id,
        acl=tuple(a for a in store.document_acl.values() if a.document_id == document.id),
    )


class _Documents:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    def _rows(self) -> list[Document]:
        return [d for d in self._s.documents.values() if d.tenant_id == self._tenant]

    async def get(self, document_id: UUID) -> Document | None:
        row = self._s.documents.get(document_id)
        return copy.deepcopy(row) if row and row.tenant_id == self._tenant else None

    async def get_by_title(self, title: str) -> Document | None:
        for row in self._rows():
            if row.title == title and row.deleted_at is None:
                return copy.deepcopy(row)
        return None

    async def list(
        self, *, reader: Reader, filters: DocumentFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[Document]:
        rows: list[Document] = []
        for row in sorted(self._rows(), key=lambda d: (d.created_at, d.id)):
            if row.deleted_at is not None or not _after((row.created_at, row.id), after):
                continue
            if not can_read(reader, _scope(self._s, row)):
                continue
            if filters.category is not None and row.category is not filters.category:
                continue
            if filters.query and filters.query.casefold() not in row.title.casefold():
                continue
            if filters.status is not None:
                versions = [v for v in self._s.document_versions.values() if v.document_id == row.id]
                current = next((v for v in versions if v.id == row.active_version_id), None) or (
                    max(versions, key=lambda v: v.version_no) if versions else None
                )
                if current is None or current.status is not filters.status:
                    continue
            rows.append(copy.deepcopy(row))
            if len(rows) >= limit:
                break
        return rows

    async def add(self, document: Document) -> None:
        if document.id in self._s.documents:
            raise ConflictError("duplicate document id")
        self._s.documents[document.id] = copy.deepcopy(document)

    async def update(self, document: Document, *, expected_version: int) -> None:
        stored = self._s.documents.get(document.id)
        if stored is None or stored.tenant_id != self._tenant or stored.version != expected_version:
            raise VersionConflict("the document was changed by someone else; reload and retry")
        document.version = expected_version + 1
        self._s.documents[document.id] = copy.deepcopy(document)


class _Versions(_TenantTable[DocumentVersion]):
    async def get(self, version_id: UUID) -> DocumentVersion | None:
        row = self._rows_dict.get(version_id)
        return copy.deepcopy(row) if row and row.tenant_id == self._tenant else None

    async def get_by_number(self, document_id: UUID, version_no: int) -> DocumentVersion | None:
        for row in self._rows():
            if row.document_id == document_id and row.version_no == version_no:
                return copy.deepcopy(row)
        return None

    async def list_for_document(self, document_id: UUID) -> list[DocumentVersion]:
        return sorted(
            (copy.deepcopy(r) for r in self._rows() if r.document_id == document_id),
            key=lambda v: v.version_no,
        )

    async def list_for_documents(self, document_ids: Sequence[UUID]) -> dict[UUID, list[DocumentVersion]]:
        wanted = set(document_ids)
        result: dict[UUID, list[DocumentVersion]] = {}
        for row in self._rows():
            if row.document_id in wanted:
                result.setdefault(row.document_id, []).append(copy.deepcopy(row))
        return result

    async def find_by_sha256(self, document_id: UUID, sha256: str) -> DocumentVersion | None:
        for row in self._rows():
            if row.document_id == document_id and row.sha256 == sha256:
                return copy.deepcopy(row)
        return None

    async def list_by_status(self, statuses: frozenset[VersionStatus]) -> list[DocumentVersion]:
        return [copy.deepcopy(r) for r in self._rows() if r.status in statuses]

    async def add(self, version: DocumentVersion) -> None:
        self._check(version)
        if any(
            r.document_id == version.document_id and r.version_no == version.version_no for r in self._rows()
        ):
            raise ConflictError("duplicate version number")
        self._rows_dict[version.id] = copy.deepcopy(version)

    async def update(self, version: DocumentVersion) -> None:
        self._check(version)
        self._rows_dict[version.id] = copy.deepcopy(version)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class _Chunks:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    def _rows(self) -> list[Chunk]:
        return [c for c in self._s.chunks.values() if c.tenant_id == self._tenant]

    async def replace_for_version(self, version_id: UUID, chunks: Sequence[Chunk]) -> None:
        for chunk_id in [c.id for c in self._rows() if c.version_id == version_id]:
            del self._s.chunks[chunk_id]
        for chunk in chunks:
            if chunk.tenant_id != self._tenant:
                raise ConflictError("tenant mismatch (RLS would reject the row)")
            self._s.chunks[chunk.id] = copy.deepcopy(chunk)

    async def get(self, chunk_id: UUID) -> Chunk | None:
        row = self._s.chunks.get(chunk_id)
        return copy.deepcopy(row) if row and row.tenant_id == self._tenant else None

    async def list_for_version(self, version_id: UUID) -> list[Chunk]:
        return sorted(
            (copy.deepcopy(c) for c in self._rows() if c.version_id == version_id), key=lambda c: c.ordinal
        )

    async def count_for_version(self, version_id: UUID) -> int:
        return sum(1 for c in self._rows() if c.version_id == version_id)

    async def set_retrievable(self, version_id: UUID, retrievable: bool) -> int:
        count = 0
        for chunk in self._rows():
            if chunk.version_id == version_id and chunk.retrievable != retrievable:
                chunk.retrievable = retrievable
                count += 1
        return count

    def _candidates(self, reader: Reader) -> list[tuple[Chunk, Document]]:
        out: list[tuple[Chunk, Document]] = []
        for chunk in self._rows():
            if not chunk.retrievable:
                continue
            version = self._s.document_versions.get(chunk.version_id)
            if version is None or not version.retrievable:
                continue
            document = self._s.documents.get(version.document_id)
            if document is None or document.deleted_at is not None:
                continue
            if can_read(reader, _scope(self._s, document)):
                out.append((chunk, document))
        return out

    async def vector_search(self, embedding: Sequence[float], *, reader: Reader, limit: int) -> list[LegHit]:
        scored = [
            (_cosine(embedding, c.embedding or []), c, d) for c, d in self._candidates(reader) if c.embedding
        ]
        scored.sort(key=lambda t: (-t[0], str(t[1].id)))
        return [
            LegHit(c.id, d.id, c.version_id, rank, score)
            for rank, (score, c, d) in enumerate(scored[:limit], 1)
        ]

    async def keyword_search(
        self, query: str, *, language: str | None, reader: Reader, limit: int
    ) -> list[LegHit]:
        terms = {t for t in _QUERY_WORD.findall(query.casefold()) if len(t) > 1}
        scored = []
        for chunk, document in self._candidates(reader):
            words = set(_QUERY_WORD.findall(chunk.content.casefold()))
            hits = len(terms & words)
            if hits:
                scored.append((float(hits), chunk, document))
        scored.sort(key=lambda t: (-t[0], str(t[1].id)))
        return [
            LegHit(c.id, d.id, c.version_id, rank, score)
            for rank, (score, c, d) in enumerate(scored[:limit], 1)
        ]


class _Acl:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    async def list_for_document(self, document_id: UUID) -> list[AclEntry]:
        return [
            copy.deepcopy(a)
            for a in self._s.document_acl.values()
            if a.document_id == document_id and a.tenant_id == self._tenant
        ]

    async def replace_for_document(self, document_id: UUID, entries: Sequence[AclEntry]) -> None:
        for key in [k for k, a in self._s.document_acl.items() if a.document_id == document_id]:
            del self._s.document_acl[key]
        for entry in entries:
            self._s.document_acl[entry.id] = copy.deepcopy(entry)


class _ReviewEvents:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    async def add(self, event: ReviewEvent) -> None:
        self._s.review_events.append(copy.deepcopy(event))

    async def list_for_version(self, version_id: UUID) -> list[ReviewEvent]:
        return [
            copy.deepcopy(e)
            for e in self._s.review_events
            if e.version_id == version_id and e.tenant_id == self._tenant
        ]


class _IndexGenerations:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._s = store
        self._tenant = tenant_id

    async def get(self, generation: str) -> IndexGeneration | None:
        if self._tenant is None:
            return None
        return self._s.index_generations.get((self._tenant, generation))

    async def upsert(self, generation: IndexGeneration) -> None:
        self._s.index_generations[(generation.tenant_id, generation.generation)] = generation

    async def list(self) -> list[IndexGeneration]:
        return [g for (t, _), g in self._s.index_generations.items() if t == self._tenant]


class _Jobs:
    """Platform-level queue (no tenant filter), mirroring `platform.jobs` semantics incl. stale reclaim."""

    def __init__(self, store: MemoryStore) -> None:
        self._s = store

    async def add(self, job: Job) -> None:
        self._s.jobs[job.id] = copy.deepcopy(job)

    async def get(self, job_id: UUID) -> Job | None:
        row = self._s.jobs.get(job_id)
        return copy.deepcopy(row) if row else None

    async def find_active_by_dedupe(self, dedupe_key: str) -> Job | None:
        for row in self._s.jobs.values():
            if row.dedupe_key == dedupe_key and row.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                return copy.deepcopy(row)
        return None

    async def claim(
        self, *, now: datetime, worker_id: str, stale_before: datetime, types: Sequence[str] | None
    ) -> Job | None:
        candidates = []
        for row in self._s.jobs.values():
            if types is not None and row.type not in types:
                continue
            queued = row.status is JobStatus.QUEUED and (row.run_after is None or row.run_after <= now)
            stale = (
                row.status is JobStatus.RUNNING and (row.heartbeat_at or row.locked_at or now) < stale_before
            )
            if queued or stale:
                candidates.append(row)
        if not candidates:
            return None
        row = min(candidates, key=lambda r: (r.run_after or r.created_at, r.created_at, str(r.id)))
        row.status = JobStatus.RUNNING
        row.locked_by = worker_id
        row.locked_at = now
        row.heartbeat_at = now
        row.attempts += 1
        row.updated_at = now
        return copy.deepcopy(row)

    async def update(self, job: Job) -> None:
        self._s.jobs[job.id] = copy.deepcopy(job)

    async def list(self, *, filters: JobFilter, limit: int) -> list[Job]:
        rows = [
            r
            for r in self._s.jobs.values()
            if (filters.status is None or r.status is filters.status)
            and (filters.type is None or r.type == filters.type)
            and (filters.tenant_id is None or r.tenant_id == filters.tenant_id)
        ]
        rows.sort(key=lambda r: (r.created_at, str(r.id)), reverse=True)
        return [copy.deepcopy(r) for r in rows[:limit]]

    async def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self._s.jobs.values():
            counts[row.status.value] = counts.get(row.status.value, 0) + 1
        return counts


# --- M5: vault, security, supporting documents, retention -----------------------------------------------------


class _Vault:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    def _rows(self) -> list[VaultEntry]:
        return [e for e in self._store.pii_vault.values() if e.tenant_id == self._tenant]

    async def get_by_hmac(self, value_hmac: bytes) -> VaultEntry | None:
        return next((e for e in self._rows() if e.value_hmac == value_hmac), None)

    async def get_by_placeholder(self, placeholder: str) -> VaultEntry | None:
        return next((e for e in self._rows() if e.placeholder == placeholder), None)

    async def next_sequence(self, entity_type: EntityType) -> int:
        if self._tenant is None:
            raise RuntimeError("vault sequences require a tenant context")
        key = (self._tenant, entity_type.value)
        value = self._store.pii_counters.get(key, 1)
        self._store.pii_counters[key] = value + 1
        return value

    async def add(self, entry: VaultEntry) -> None:
        if any(e.value_hmac == entry.value_hmac for e in self._rows()):
            raise ConflictError("duplicate vault entry")
        self._store.pii_vault[entry.id] = entry

    async def update(self, entry: VaultEntry) -> None:
        if entry.id in self._store.pii_vault:
            self._store.pii_vault[entry.id] = entry

    async def count(self) -> int:
        return len(self._rows())


class _SecurityDefaults:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def get(self) -> SecurityDefaults:
        return self._store.security_defaults

    async def update(self, defaults: SecurityDefaults) -> None:
        self._store.security_defaults = defaults


class _SecurityPolicies:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    async def get(self) -> SecurityPolicy | None:
        return self._store.security_policies.get(self._tenant) if self._tenant else None

    async def upsert(self, policy: SecurityPolicy) -> None:
        if policy.tenant_id != self._tenant:
            raise RuntimeError("security policy tenant does not match the unit of work")
        self._store.security_policies[policy.tenant_id] = policy


class _InjectionFindings:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    async def add_many(self, findings: Sequence[InjectionFinding]) -> None:
        self._store.injection_findings.extend(findings)

    async def list_for_subject(self, subject_type: str, subject_id: str) -> list[InjectionFinding]:
        return [
            f
            for f in self._store.injection_findings
            if f.tenant_id == self._tenant and f.subject_type == subject_type and f.subject_id == subject_id
        ]


class _SupportingDocuments:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    def _rows(self) -> list[SupportingDocument]:
        rows = [d for d in self._store.supporting_documents.values() if d.tenant_id == self._tenant]
        return sorted(rows, key=lambda d: (d.created_at, d.id))

    async def get(self, document_id: UUID) -> SupportingDocument | None:
        return next((d for d in self._rows() if d.id == document_id), None)

    async def list_for_request(self, request_id: UUID) -> list[SupportingDocument]:
        return [d for d in self._rows() if d.request_id == request_id]

    async def count_for_requests(self, request_ids: Sequence[UUID]) -> dict[UUID, int]:
        wanted = set(request_ids)
        counts: dict[UUID, int] = {}
        for d in self._rows():
            if d.request_id in wanted and d.status != "deleted":
                counts[d.request_id] = counts.get(d.request_id, 0) + 1
        return counts

    async def list_by_status(self, status: str) -> list[SupportingDocument]:
        return [d for d in self._rows() if d.status == status]

    async def add(self, document: SupportingDocument) -> None:
        if document.request_id not in self._store.leave_requests:
            raise ConflictError("unknown request")
        self._store.supporting_documents[document.id] = document

    async def update(self, document: SupportingDocument) -> None:
        if document.id in self._store.supporting_documents:
            self._store.supporting_documents[document.id] = document


class _SupportingAccess:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    async def add(self, event: SupportingAccessEvent) -> None:
        self._store.supporting_access.append(event)

    async def list_for_document(self, document_id: UUID) -> list[SupportingAccessEvent]:
        return [
            e
            for e in self._store.supporting_access
            if e.tenant_id == self._tenant and e.supporting_document_id == document_id
        ]


class _DownloadTokens:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def add(self, token: DownloadToken) -> None:
        self._store.download_tokens[token.token_hash] = token

    async def consume(self, token_hash: str, now: datetime) -> DownloadToken | None:
        token = self._store.download_tokens.get(token_hash)
        if token is None or token.used_at is not None or token.expires_at <= now:
            return None
        token.used_at = now
        return token


class _RetentionPolicies:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    def _rows(self) -> list[RetentionPolicy]:
        rows = [p for p in self._store.retention_policies.values() if p.tenant_id == self._tenant]
        return sorted(rows, key=lambda p: p.document_class.value)

    async def list(self) -> list[RetentionPolicy]:
        return self._rows()

    async def get(self, policy_id: UUID) -> RetentionPolicy | None:
        return next((p for p in self._rows() if p.id == policy_id), None)

    async def get_by_class(self, document_class: DocumentClass) -> RetentionPolicy | None:
        return next((p for p in self._rows() if p.document_class is document_class), None)

    async def add(self, policy: RetentionPolicy) -> None:
        if any(p.document_class is policy.document_class for p in self._rows()):
            raise ConflictError("duplicate retention policy")
        self._store.retention_policies[policy.id] = policy

    async def update(self, policy: RetentionPolicy) -> None:
        if policy.id in self._store.retention_policies:
            self._store.retention_policies[policy.id] = policy

    async def delete(self, policy_id: UUID) -> None:
        if any(i.policy_id == policy_id for i in self._store.retention_items.values()):
            raise ConflictError("the policy has review items")
        self._store.retention_policies.pop(policy_id, None)


class _RetentionItems:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    def _rows(self) -> list[RetentionReviewItem]:
        rows = [i for i in self._store.retention_items.values() if i.tenant_id == self._tenant]
        return sorted(rows, key=lambda i: (i.created_at, i.id))

    async def list(self, *, status: ReviewStatus | None) -> list[RetentionReviewItem]:
        return [i for i in self._rows() if status is None or i.status is status]

    async def get(self, item_id: UUID) -> RetentionReviewItem | None:
        return next((i for i in self._rows() if i.id == item_id), None)

    async def latest_for(self, subject_type: str, subject_id: UUID) -> RetentionReviewItem | None:
        rows = [i for i in self._rows() if i.subject_type == subject_type and i.subject_id == subject_id]
        return rows[-1] if rows else None

    async def add(self, item: RetentionReviewItem) -> None:
        if item.policy_id not in self._store.retention_policies:
            raise ConflictError("unknown retention policy")
        self._store.retention_items[item.id] = item

    async def update(self, item: RetentionReviewItem) -> None:
        if item.id in self._store.retention_items:
            self._store.retention_items[item.id] = item


class _Conversations:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    def _rows(self) -> list[Conversation]:
        return [c for c in self._store.conversations.values() if c.tenant_id == self._tenant]

    async def get(self, conversation_id: UUID) -> Conversation | None:
        return next((c for c in self._rows() if c.id == conversation_id), None)

    async def list_for_employee(
        self, employee_id: UUID, *, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[Conversation]:
        rows = [
            c for c in self._rows() if c.employee_id == employee_id and c.status is ConversationStatus.OPEN
        ]
        rows.sort(key=lambda c: (c.last_activity_at, c.id), reverse=True)
        if after is not None:
            rows = [c for c in rows if (c.last_activity_at, c.id) < after]
        return rows[:limit]

    async def add(self, conversation: Conversation) -> None:
        if conversation.employee_id not in self._store.employees:
            raise ConflictError("unknown employee")
        self._store.conversations[conversation.id] = conversation

    async def update(self, conversation: Conversation) -> None:
        if conversation.id in self._store.conversations:
            self._store.conversations[conversation.id] = conversation

    async def try_lock(self, conversation_id: UUID, *, until: datetime, now: datetime) -> bool:
        conversation = await self.get(conversation_id)
        if conversation is None or (
            conversation.run_lock_until is not None and conversation.run_lock_until >= now
        ):
            return False
        conversation.run_lock_until = until
        return True

    async def unlock(self, conversation_id: UUID) -> None:
        conversation = await self.get(conversation_id)
        if conversation is not None:
            conversation.run_lock_until = None

    async def expired(self, *, inactive_before: datetime) -> list[Conversation]:
        return [
            c
            for c in self._rows()
            if c.last_activity_at < inactive_before or c.status is ConversationStatus.ARCHIVED
        ]

    async def delete(self, conversation_id: UUID) -> None:
        if await self.get(conversation_id) is None:
            return
        self._store.conversations.pop(conversation_id, None)
        self._store.messages = [m for m in self._store.messages if m.conversation_id != conversation_id]
        self._store.agent_runs = [r for r in self._store.agent_runs if r.conversation_id != conversation_id]
        self._store.proposals = {
            k: v for k, v in self._store.proposals.items() if v.conversation_id != conversation_id
        }
        self._store.pending_interrupts.pop(conversation_id, None)


class _Messages:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    async def add(self, message: ConversationMessage) -> None:
        if message.conversation_id not in self._store.conversations:
            raise ConflictError("unknown conversation")
        self._store.messages.append(message)

    async def list_for_conversation(self, conversation_id: UUID) -> list[ConversationMessage]:
        rows = [
            m
            for m in self._store.messages
            if m.tenant_id == self._tenant and m.conversation_id == conversation_id
        ]
        return sorted(rows, key=lambda m: (m.created_at, m.id))


class _AgentRuns:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    async def add(self, run: AgentRun) -> None:
        self._store.agent_runs.append(run)

    async def count_for_conversation(self, conversation_id: UUID) -> int:
        return len(
            [
                r
                for r in self._store.agent_runs
                if r.tenant_id == self._tenant and r.conversation_id == conversation_id
            ]
        )

    async def list_for_conversation(self, conversation_id: UUID) -> list[AgentRun]:
        rows = [
            r
            for r in self._store.agent_runs
            if r.tenant_id == self._tenant and r.conversation_id == conversation_id
        ]
        return sorted(rows, key=lambda r: (r.created_at, r.turn))


class _ProviderEvents:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    async def add_many(self, events: Sequence[ProviderEventRecord]) -> None:
        self._store.provider_events.extend(events)


class _Proposals:
    """`claim` reads and writes the shared store directly — the in-memory stand-in for `UPDATE … WHERE
    status = 'pending' RETURNING`, so two concurrent units of work cannot both claim one proposal."""

    def __init__(self, work: MemoryStore, shared: MemoryStore, tenant_id: UUID | None) -> None:
        self._work = work
        self._shared = shared
        self._tenant = tenant_id

    def _rows(self) -> list[ActionProposal]:
        return [p for p in self._work.proposals.values() if p.tenant_id == self._tenant]

    async def add(self, proposal: ActionProposal) -> None:
        if proposal.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        if proposal.conversation_id not in self._work.conversations:
            raise ConflictError("unknown conversation")
        self._work.proposals[proposal.id] = copy.deepcopy(proposal)

    async def get(self, proposal_id: UUID) -> ActionProposal | None:
        return copy.deepcopy(next((p for p in self._rows() if p.id == proposal_id), None))

    async def update(self, proposal: ActionProposal) -> None:
        if proposal.tenant_id != self._tenant or proposal.id not in self._work.proposals:
            return
        self._work.proposals[proposal.id] = copy.deepcopy(proposal)

    async def claim(self, proposal_id: UUID, *, run_id: UUID, now: datetime) -> ActionProposal | None:
        current = self._shared.proposals.get(proposal_id)
        if (
            current is None
            or current.tenant_id != self._tenant
            or current.status is not ProposalStatus.PENDING
            or current.expires_at <= now
        ):
            return None
        for store in (self._shared, self._work):
            row = store.proposals.get(proposal_id)
            if row is not None:
                row.status, row.claimed_by_run, row.claimed_at = ProposalStatus.CONFIRMED, run_id, now
        return copy.deepcopy(self._work.proposals.get(proposal_id) or current)

    async def list_for_conversation(self, conversation_id: UUID) -> list[ActionProposal]:
        rows = [p for p in self._rows() if p.conversation_id == conversation_id]
        return [copy.deepcopy(p) for p in sorted(rows, key=lambda p: (p.created_at, p.id))]

    async def due_for_expiry(self, now: datetime) -> list[ActionProposal]:
        return [
            copy.deepcopy(p)
            for p in self._rows()
            if p.status is ProposalStatus.PENDING and p.expires_at <= now
        ]


class _PendingInterrupts:
    def __init__(self, store: MemoryStore, tenant_id: UUID | None) -> None:
        self._store = store
        self._tenant = tenant_id

    async def put(self, interrupt: PendingInterrupt) -> None:
        if interrupt.tenant_id != self._tenant:
            raise ConflictError("RLS: WITH CHECK failed")
        self._store.pending_interrupts[interrupt.conversation_id] = interrupt

    async def get(self, conversation_id: UUID) -> PendingInterrupt | None:
        row = self._store.pending_interrupts.get(conversation_id)
        return row if row is not None and row.tenant_id == self._tenant else None

    async def delete(self, conversation_id: UUID) -> None:
        row = self._store.pending_interrupts.get(conversation_id)
        if row is not None and row.tenant_id == self._tenant:
            del self._store.pending_interrupts[conversation_id]


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
    documents: DocumentRepository
    document_versions: DocumentVersionRepository
    chunks: ChunkRepository
    document_acl: AclRepository
    review_events: ReviewEventRepository
    index_generations: IndexGenerationRepository
    jobs: JobRepository
    pii_vault: VaultRepository
    security_defaults: SecurityDefaultsRepository
    security_policies: SecurityPolicyRepository
    injection_findings: InjectionFindingRepository
    supporting_documents: SupportingDocumentRepository
    supporting_access: SupportingAccessRepository
    download_tokens: DownloadTokenRepository
    retention_policies: RetentionPolicyRepository
    retention_items: RetentionReviewItemRepository
    conversations: ConversationRepository
    messages: MessageRepository
    agent_runs: AgentRunRepository
    provider_events: ProviderEventRepository
    proposals: ProposalRepository
    pending_interrupts: PendingInterruptRepository

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
        self.documents = _Documents(self._work, tenant)
        self.document_versions = _Versions(self._work.document_versions, tenant)
        self.chunks = _Chunks(self._work, tenant)
        self.document_acl = _Acl(self._work, tenant)
        self.review_events = _ReviewEvents(self._work, tenant)
        self.index_generations = _IndexGenerations(self._work, tenant)
        self.jobs = _Jobs(self._work)
        self.pii_vault = _Vault(self._work, tenant)
        self.security_defaults = _SecurityDefaults(self._work)
        self.security_policies = _SecurityPolicies(self._work, tenant)
        self.injection_findings = _InjectionFindings(self._work, tenant)
        self.supporting_documents = _SupportingDocuments(self._work, tenant)
        self.supporting_access = _SupportingAccess(self._work, tenant)
        self.download_tokens = _DownloadTokens(self._work)
        self.retention_policies = _RetentionPolicies(self._work, tenant)
        self.retention_items = _RetentionItems(self._work, tenant)
        self.conversations = _Conversations(self._work, tenant)
        self.messages = _Messages(self._work, tenant)
        self.agent_runs = _AgentRuns(self._work, tenant)
        self.provider_events = _ProviderEvents(self._work, tenant)
        self.proposals = _Proposals(self._work, self._shared, tenant)
        self.pending_interrupts = _PendingInterrupts(self._work, tenant)
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
