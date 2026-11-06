"""SQL repositories for leave requests, approval steps, request events and request numbers."""

from __future__ import annotations

import builtins
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Select, and_, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.leave.errors import VersionConflict
from leave_assistant.application.ports import RequestFilter
from leave_assistant.domain.leave.request import (
    ActorKind,
    ApprovalStep,
    DecisionChannel,
    LeaveRequest,
    RequestEventRecord,
    StepStatus,
)
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.leave.types import StepRole
from leave_assistant.infrastructure.db.models import (
    ApprovalStepRow,
    EmployeeRow,
    LeaveRequestEventRow,
    LeaveRequestRow,
)
from leave_assistant.infrastructure.db.session import flush_or_conflict

REQUEST_FIELDS = (
    "request_number",
    "employee_id",
    "leave_type_id",
    "start_date",
    "end_date",
    "working_days",
    "reason",
    "submitted_at",
    "decided_at",
    "cancelled_at",
    "modification_of_request_id",
    "idempotency_key",
    "created_at",
    "updated_at",
)


def _request_to_domain(r: LeaveRequestRow) -> LeaveRequest:
    return LeaveRequest(
        id=r.id,
        tenant_id=r.tenant_id,
        request_number=r.request_number,
        employee_id=r.employee_id,
        leave_type_id=r.leave_type_id,
        start_date=r.start_date,
        end_date=r.end_date,
        working_days=r.working_days,
        status=RequestStatus(r.status),
        reason=r.reason,
        submitted_at=r.submitted_at,
        decided_at=r.decided_at,
        cancelled_at=r.cancelled_at,
        modification_of_request_id=r.modification_of_request_id,
        idempotency_key=r.idempotency_key,
        version=r.version,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


class SqlLeaveRequestRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[LeaveRequestRow]:
        return select(LeaveRequestRow).where(LeaveRequestRow.tenant_id == self._tenant)

    async def get(self, request_id: UUID) -> LeaveRequest | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(LeaveRequestRow.id == request_id))
        ).scalar_one_or_none()
        return _request_to_domain(row) if row else None

    async def list(
        self, *, filters: RequestFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> builtins.list[LeaveRequest]:
        if self._tenant is None:
            return []
        stmt = self._base().order_by(LeaveRequestRow.created_at, LeaveRequestRow.id).limit(limit)
        if filters.employee_id is not None:
            stmt = stmt.where(LeaveRequestRow.employee_id == filters.employee_id)
        if filters.department_id is not None:
            members = select(EmployeeRow.id).where(
                EmployeeRow.tenant_id == self._tenant, EmployeeRow.department_id == filters.department_id
            )
            stmt = stmt.where(LeaveRequestRow.employee_id.in_(members))
        if filters.statuses is not None:
            stmt = stmt.where(LeaveRequestRow.status.in_([s.value for s in filters.statuses]))
        if filters.date_from is not None:
            stmt = stmt.where(LeaveRequestRow.end_date >= filters.date_from)
        if filters.date_to is not None:
            stmt = stmt.where(LeaveRequestRow.start_date <= filters.date_to)
        if after is not None:
            stmt = stmt.where(tuple_(LeaveRequestRow.created_at, LeaveRequestRow.id) > after)
        return [_request_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def get_by_number(self, request_number: str) -> LeaveRequest | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(LeaveRequestRow.request_number == request_number))
        ).scalar_one_or_none()
        return _request_to_domain(row) if row else None

    async def find_by_idempotency_key(self, employee_id: UUID, key: str) -> LeaveRequest | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(
                self._base().where(
                    LeaveRequestRow.employee_id == employee_id, LeaveRequestRow.idempotency_key == key
                )
            )
        ).scalar_one_or_none()
        return _request_to_domain(row) if row else None

    async def list_for_employee(self, employee_id: UUID) -> builtins.list[LeaveRequest]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(LeaveRequestRow.employee_id == employee_id)
            .order_by(LeaveRequestRow.start_date)
        )
        return [_request_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def list_in_window(
        self, start: date, end: date, *, statuses: frozenset[RequestStatus]
    ) -> builtins.list[LeaveRequest]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(
                LeaveRequestRow.status.in_([s.value for s in statuses]),
                LeaveRequestRow.start_date <= end,
                LeaveRequestRow.end_date >= start,
            )
            .order_by(LeaveRequestRow.start_date)
        )
        return [_request_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def list_by_status(self, statuses: frozenset[RequestStatus]) -> builtins.list[LeaveRequest]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(LeaveRequestRow.status.in_([s.value for s in statuses]))
            .order_by(LeaveRequestRow.start_date)
        )
        return [_request_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, request: LeaveRequest) -> None:
        row = LeaveRequestRow(
            id=request.id, tenant_id=request.tenant_id, status=request.status.value, version=request.version
        )
        for field in REQUEST_FIELDS:
            setattr(row, field, getattr(request, field))
        self._s.add(row)
        await flush_or_conflict(self._s)

    async def update(self, request: LeaveRequest, *, expected_version: int, bump: bool = True) -> None:
        row = (
            await self._s.execute(self._base().where(LeaveRequestRow.id == request.id).with_for_update())
        ).scalar_one_or_none()
        if row is None or row.version != expected_version:
            raise VersionConflict("the request was changed by someone else; reload and retry")
        for field in REQUEST_FIELDS:
            setattr(row, field, getattr(request, field))
        row.status = request.status.value
        row.version = expected_version + 1 if bump else expected_version
        request.version = row.version
        await flush_or_conflict(self._s)

    async def lock_employee(self, employee_id: UUID) -> None:
        """Serialises postings and overlap checks per employee (ADR-0005 balance scope)."""
        await self._s.execute(
            text(
                "SELECT id FROM app.employees WHERE id = :employee_id AND tenant_id = :tenant_id FOR UPDATE"
            ),
            {"employee_id": employee_id, "tenant_id": self._tenant},
        )

    async def next_request_number(self, year: int) -> str:
        row = (
            await self._s.execute(
                text(
                    "INSERT INTO app.request_counters (tenant_id, year, next_seq) VALUES (:tenant_id, :year, 1) "
                    "ON CONFLICT (tenant_id, year) DO UPDATE SET next_seq = app.request_counters.next_seq + 1 RETURNING next_seq"
                ),
                {"tenant_id": self._tenant, "year": year},
            )
        ).scalar_one()
        return f"LR-{year}-{int(row):06d}"


STEP_FIELDS = (
    "request_id",
    "step_no",
    "designated_employee_id",
    "decided_by_employee_id",
    "decided_at",
    "comment",
    "fallback",
    "created_at",
    "updated_at",
)


def _step_to_domain(r: ApprovalStepRow) -> ApprovalStep:
    return ApprovalStep(
        id=r.id,
        tenant_id=r.tenant_id,
        request_id=r.request_id,
        step_no=r.step_no,
        approver_role=StepRole(r.approver_role),
        designated_employee_id=r.designated_employee_id,
        status=StepStatus(r.status),
        decided_by_employee_id=r.decided_by_employee_id,
        decided_at=r.decided_at,
        comment=r.comment,
        via=DecisionChannel(r.via) if r.via else None,
        fallback=r.fallback,
        version=r.version,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


class SqlApprovalStepRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[ApprovalStepRow]:
        return select(ApprovalStepRow).where(ApprovalStepRow.tenant_id == self._tenant)

    async def get(self, step_id: UUID) -> ApprovalStep | None:
        if self._tenant is None:
            return None
        row = (await self._s.execute(self._base().where(ApprovalStepRow.id == step_id))).scalar_one_or_none()
        return _step_to_domain(row) if row else None

    async def list_for_request(self, request_id: UUID) -> builtins.list[ApprovalStep]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(ApprovalStepRow.request_id == request_id)
            .order_by(ApprovalStepRow.step_no, ApprovalStepRow.created_at)
        )
        return [_step_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def list_pending(
        self, *, designated_employee_id: UUID | None = None, role: StepRole | None = None
    ) -> builtins.list[ApprovalStep]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(ApprovalStepRow.status == StepStatus.PENDING.value)
            .order_by(ApprovalStepRow.created_at, ApprovalStepRow.step_no)
        )
        if designated_employee_id is not None:
            stmt = stmt.where(ApprovalStepRow.designated_employee_id == designated_employee_id)
        if role is not None:
            stmt = stmt.where(ApprovalStepRow.approver_role == role.value)
        return [_step_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, step: ApprovalStep) -> None:
        row = ApprovalStepRow(
            id=step.id,
            tenant_id=step.tenant_id,
            approver_role=step.approver_role.value,
            status=step.status.value,
            via=step.via.value if step.via else None,
            version=step.version,
        )
        for field in STEP_FIELDS:
            setattr(row, field, getattr(step, field))
        self._s.add(row)
        await flush_or_conflict(self._s)

    async def update(self, step: ApprovalStep, *, expected_version: int) -> None:
        row = (
            await self._s.execute(self._base().where(ApprovalStepRow.id == step.id).with_for_update())
        ).scalar_one_or_none()
        if row is None or row.version != expected_version:
            raise VersionConflict("the step was changed by someone else; reload and retry")
        for field in STEP_FIELDS:
            setattr(row, field, getattr(step, field))
        row.status = step.status.value
        row.via = step.via.value if step.via else None
        row.version = expected_version + 1
        step.version = row.version
        await flush_or_conflict(self._s)


class SqlRequestEventRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add(self, event: RequestEventRecord) -> None:
        self._s.add(
            LeaveRequestEventRow(
                id=event.id,
                tenant_id=event.tenant_id,
                request_id=event.request_id,
                event_type=event.event_type,
                from_status=event.from_status.value if event.from_status else None,
                to_status=event.to_status.value,
                actor_employee_id=event.actor_employee_id,
                actor_kind=event.actor_kind.value,
                payload=event.payload,
                occurred_at=event.occurred_at,
            )
        )
        await flush_or_conflict(self._s)

    async def list_for_request(self, request_id: UUID) -> builtins.list[RequestEventRecord]:
        if self._tenant is None:
            return []
        stmt = (
            select(LeaveRequestEventRow)
            .where(
                LeaveRequestEventRow.tenant_id == self._tenant, LeaveRequestEventRow.request_id == request_id
            )
            .order_by(LeaveRequestEventRow.occurred_at, LeaveRequestEventRow.id)
        )
        return [_event_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def find_by_proposal(self, proposal_id: UUID) -> RequestEventRecord | None:
        if self._tenant is None:
            return None
        stmt = (
            select(LeaveRequestEventRow)
            .where(
                LeaveRequestEventRow.tenant_id == self._tenant,
                LeaveRequestEventRow.payload["proposal_id"].astext == str(proposal_id),
            )
            .order_by(LeaveRequestEventRow.occurred_at)
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _event_to_domain(row) if row else None


def _event_to_domain(r: LeaveRequestEventRow) -> RequestEventRecord:
    return RequestEventRecord(
        id=r.id,
        tenant_id=r.tenant_id,
        request_id=r.request_id,
        event_type=r.event_type,
        from_status=RequestStatus(r.from_status) if r.from_status else None,
        to_status=RequestStatus(r.to_status),
        actor_employee_id=r.actor_employee_id,
        actor_kind=ActorKind(r.actor_kind),
        occurred_at=r.occurred_at,
        payload=dict(r.payload),
    )


__all__ = [
    "SqlApprovalStepRepository",
    "SqlLeaveRequestRepository",
    "SqlRequestEventRepository",
    "and_",
    "or_",
]
