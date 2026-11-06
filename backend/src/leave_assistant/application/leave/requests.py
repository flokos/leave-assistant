"""Leave request use cases (FR-LEAVE-04…08): validate, submit, modify, cancel/withdraw, read (doc 09 §2.3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, ResourceRef, authorize, require
from leave_assistant.application.errors import AuthorizationError, ConflictError, NotFoundError
from leave_assistant.application.leave.commands import CancelRequest, ModifyRequest, SubmitRequest
from leave_assistant.application.leave.common import (
    EventWriter,
    load_employee,
    load_leave_type,
    load_tenant,
    tenant_user,
)
from leave_assistant.application.leave.errors import LeaveValidationError, VersionConflict
from leave_assistant.application.leave.ledger import LedgerService
from leave_assistant.application.leave.planner import Plan, RoutingPlanner
from leave_assistant.application.leave.views import RequestDetail, leave_type_visible, reason_visible
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.pagination import Page, build_page, clamp_limit, decode_cursor
from leave_assistant.application.ports import Clock, IdGenerator, RequestFilter, UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.ledger import EntryType, deduction_key, reversal_key
from leave_assistant.domain.leave.request import ApprovalStep, LeaveRequest, StepStatus
from leave_assistant.domain.leave.rules import RoutingDecision, StepRole
from leave_assistant.domain.leave.state_machine import (
    DEDUCTED_STATUSES,
    PENDING_STATUSES,
    InvalidTransition,
    RequestEvent,
    RequestStatus,
    transition,
)
from leave_assistant.domain.leave.types import LeaveType
from leave_assistant.domain.leave.validation import ValidationReport
from leave_assistant.domain.leave.working_days import classify, split_by_year
from leave_assistant.domain.tenancy import Employee, Principal, Role, Tenant

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Period:
    year: int
    start: date
    end: date
    working_days: int


class RequestWorkflow:
    """Transition helpers shared by the request and approval services (routing, deductions, reversals)."""

    def __init__(
        self,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        notifications: NotificationService,
        ledger: LedgerService,
        planner: RoutingPlanner,
    ) -> None:
        self.ids = ids
        self.clock = clock
        self.audit = audit
        self.notifications = notifications
        self.ledger = ledger
        self.planner = planner
        self.events = EventWriter(ids, clock)

    async def periods(
        self, uow: UnitOfWork, tenant: Tenant, request: LeaveRequest, *, from_date: date | None = None
    ) -> list[Period]:
        start = max(request.start_date, from_date) if from_date else request.start_date
        if start > request.end_date:
            return []
        years = tuple(range(start.year, request.end_date.year + 1))
        holidays = await self.planner.holidays_for(uow, years)
        return [
            Period(
                seg_start.year,
                seg_start,
                seg_end,
                classify(seg_start, seg_end, work_week=tenant.settings.work_week, holidays=holidays).count,
            )
            for seg_start, seg_end in split_by_year(start, request.end_date)
        ]

    async def deduct(
        self, uow: UnitOfWork, tenant: Tenant, request: LeaveRequest, leave_type: LeaveType
    ) -> None:
        if not leave_type.balance_bearing:
            return
        for period in await self.periods(uow, tenant, request):
            await self.ledger.post(
                uow,
                employee_id=request.employee_id,
                leave_type_id=leave_type.id,
                period_year=period.year,
                entry_type=EntryType.DEDUCTION,
                quantity_days=-Decimal(period.working_days),
                effective_date=period.start,
                key=deduction_key(request.id, period.year),
                request_id=request.id,
                reason=request.request_number,
            )

    async def reverse(
        self,
        uow: UnitOfWork,
        tenant: Tenant,
        request: LeaveRequest,
        leave_type: LeaveType,
        *,
        from_date: date | None = None,
        suffix: str = "",
    ) -> Decimal:
        """Reverse the (unused part of the) deduction; returns the reversed quantity."""
        if not leave_type.balance_bearing:
            return ZERO
        total = ZERO
        for period in await self.periods(uow, tenant, request, from_date=from_date):
            if period.working_days == 0:
                continue
            await self.ledger.post(
                uow,
                employee_id=request.employee_id,
                leave_type_id=leave_type.id,
                period_year=period.year,
                entry_type=EntryType.REVERSAL,
                quantity_days=Decimal(period.working_days),
                effective_date=period.start,
                key=reversal_key(request.id, period.year, suffix=suffix),
                request_id=request.id,
                reason=request.request_number,
            )
            total += period.working_days
        return total

    async def hr_admin_ids(self, uow: UnitOfWork) -> list[UUID]:
        return [e.id for e in await uow.employees.list_active(role=Role.HR_ADMIN)]

    async def route(
        self,
        uow: UnitOfWork,
        *,
        tenant: Tenant,
        request: LeaveRequest,
        requester: Employee,
        leave_type: LeaveType,
        routing: RoutingDecision,
        principal: Principal | None,
        now: datetime,
    ) -> list[ApprovalStep]:
        """Apply the routing decision to a SUBMITTED request: steps, status, events, notifications, auto-ack ledger."""
        params = {
            "request_id": str(request.id),
            "request_number": request.request_number,
            "leave_type": leave_type.code,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "working_days": str(request.working_days),
        }
        if routing.auto_acknowledged:
            request.status = transition(request.status, RequestEvent.AUTO_APPROVE)
            request.decided_at = now
            await self.events.record(
                uow,
                request,
                "auto_acknowledged",
                from_status=RequestStatus.SUBMITTED,
                to_status=request.status,
                principal=None,
                payload={"rule_id": str(routing.rule.id)},
                at=now,
            )
            await self.deduct(uow, tenant, request, leave_type)
            await self.notifications.notify(
                uow, [requester.id], "request.approved", params | {"auto_acknowledged": True}
            )
            department = await uow.departments.get(requester.department_id)
            manager_id = requester.manager_override_employee_id or (
                department.manager_employee_id if department else None
            )
            watchers: list[UUID] = (
                [manager_id] if manager_id is not None and manager_id != requester.id else []
            )
            await self.notifications.notify(
                uow, [*watchers, *await self.hr_admin_ids(uow)], "sick.auto_acknowledged", dict(params)
            )
            return []
        steps: list[ApprovalStep] = []
        for spec in routing.steps:
            steps.append(
                ApprovalStep(
                    id=self.ids.new_id(),
                    tenant_id=request.tenant_id,
                    request_id=request.id,
                    step_no=spec.step_no,
                    approver_role=spec.approver_role,
                    designated_employee_id=spec.designated_employee_id,
                    status=StepStatus.PENDING,
                    decided_by_employee_id=None,
                    decided_at=None,
                    comment=None,
                    via=None,
                    fallback=False,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await uow.approval_steps.add(steps[-1])
        first = steps[0]
        event = (
            RequestEvent.ROUTE_MANAGER if first.approver_role is StepRole.MANAGER else RequestEvent.ROUTE_HR
        )
        request.status = transition(request.status, event)
        await self.events.record(
            uow,
            request,
            "routed",
            from_status=RequestStatus.SUBMITTED,
            to_status=request.status,
            principal=principal,
            payload={
                "rule_id": str(routing.rule.id),
                "steps": [
                    {
                        "step_no": s.step_no,
                        "role": s.approver_role.value,
                        "designated_employee_id": str(s.designated_employee_id)
                        if s.designated_employee_id
                        else None,
                    }
                    for s in steps
                ],
            },
            at=now,
        )
        await self.notify_step(uow, first, params)
        return steps

    async def notify_step(self, uow: UnitOfWork, step: ApprovalStep, params: Mapping[str, object]) -> None:
        recipients = (
            [step.designated_employee_id] if step.designated_employee_id else await self.hr_admin_ids(uow)
        )
        await self.notifications.notify(
            uow,
            [r for r in recipients if r is not None],
            "approval.pending",
            {**params, "step_no": step.step_no, "approver_role": step.approver_role.value},
        )

    async def detail(self, uow: UnitOfWork, principal: Principal, request: LeaveRequest) -> RequestDetail:
        leave_type = await uow.leave_types.get(request.leave_type_id)
        employee = await load_employee(uow, request.employee_id)
        assert leave_type is not None
        steps = await uow.approval_steps.list_for_request(request.id)
        events = await uow.request_events.list_for_request(request.id)
        return RequestDetail(
            request,
            leave_type,
            employee,
            steps,
            events,
            reason_visible(principal, request, leave_type),
            leave_type_visible(principal, request),
            supporting_count=await supporting_count(uow, principal, request),
        )


async def supporting_count(uow: UnitOfWork, principal: Principal, request: LeaveRequest) -> int:
    """M5 (doc 08 §9): only the owner and HR learn that supporting documents exist."""
    if request.employee_id != principal.employee_id and Role.HR_ADMIN not in principal.roles:
        return 0
    counts = await uow.supporting_documents.count_for_requests([request.id])
    return counts.get(request.id, 0)


class LeaveRequestService:
    def __init__(self, uow_factory: UnitOfWorkFactory, workflow: RequestWorkflow) -> None:
        self._uow = uow_factory
        self._wf = workflow

    # -- reads ----------------------------------------------------------------------------------------------
    async def list(
        self,
        principal: Principal,
        *,
        statuses: frozenset[RequestStatus] | None,
        date_from: date | None,
        date_to: date | None,
        limit: int | None,
        cursor: str | None,
    ) -> Page[LeaveRequest]:
        tenant_id, employee_id = tenant_user(principal)
        require(
            principal,
            Action.READ_OWN_LEAVE,
            ResourceRef("leave_request", tenant_id=tenant_id, owner_employee_id=employee_id),
        )
        size = clamp_limit(limit)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            rows = await uow.leave_requests.list(
                filters=RequestFilter(
                    employee_id=employee_id, statuses=statuses, date_from=date_from, date_to=date_to
                ),
                limit=size + 1,
                after=decode_cursor(cursor),
            )
        return build_page(rows, size, lambda r: (r.created_at, r.id))

    async def get(self, principal: Principal, request_id: UUID) -> RequestDetail:
        tenant_id, employee_id = tenant_user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            request = await uow.leave_requests.get(request_id)
            if request is None:
                raise NotFoundError("request not found")
            await self._assert_visible(uow, principal, request)
            return await self._wf.detail(uow, principal, request)

    async def _assert_visible(self, uow: UnitOfWork, principal: Principal, request: LeaveRequest) -> None:
        if request.employee_id == principal.employee_id:
            return
        if (
            authorize(
                principal,
                Action.READ_TEAM_REQUESTS,
                ResourceRef("leave_request", tenant_id=request.tenant_id),
            ).allowed
            and Role.HR_ADMIN in principal.roles
        ):
            return
        steps = await uow.approval_steps.list_for_request(request.id)
        if any(s.designated_employee_id == principal.employee_id for s in steps):
            return
        requester = await load_employee(uow, request.employee_id)
        department = await uow.departments.get(requester.department_id)
        resource = ResourceRef(
            "leave_request",
            request.id,
            tenant_id=request.tenant_id,
            owner_employee_id=request.employee_id,
            department_id=requester.department_id,
            department_manager_employee_id=department.manager_employee_id if department else None,
        )
        if authorize(principal, Action.READ_TEAM_REQUESTS, resource).allowed:
            return
        raise AuthorizationError(
            "not permitted to read this request",
            action=Action.READ_TEAM_REQUESTS.value,
            resource=resource.describe(),
        )

    # -- validate / submit ----------------------------------------------------------------------------------
    async def validate(self, principal: Principal, cmd: SubmitRequest) -> ValidationReport:
        tenant_id, employee_id = tenant_user(principal)
        require(
            principal,
            Action.SUBMIT_OWN_REQUEST,
            ResourceRef("leave_request", tenant_id=tenant_id, owner_employee_id=employee_id),
        )
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            plan = await self._plan(
                uow, tenant_id, employee_id, cmd.leave_type_code, cmd.start_date, cmd.end_date
            )
        return plan.report

    async def _plan(
        self,
        uow: UnitOfWork,
        tenant_id: UUID,
        employee_id: UUID,
        leave_type_code: str,
        start: date,
        end: date,
        *,
        exclude_request_id: UUID | None = None,
        credit: dict[int, Decimal] | None = None,
    ) -> Plan:
        tenant = await load_tenant(uow, tenant_id)
        employee = await load_employee(uow, employee_id)
        leave_type = await load_leave_type(uow, leave_type_code)
        return await self._wf.planner.plan(
            uow,
            tenant=tenant,
            employee=employee,
            leave_type=leave_type,
            start=start,
            end=end,
            exclude_request_id=exclude_request_id,
            credit_days_by_year=credit,
        )

    async def submit(self, principal: Principal, cmd: SubmitRequest) -> RequestDetail:
        tenant_id, employee_id = tenant_user(principal)
        require(
            principal,
            Action.SUBMIT_OWN_REQUEST,
            ResourceRef("leave_request", tenant_id=tenant_id, owner_employee_id=employee_id),
        )
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await uow.leave_requests.lock_employee(employee_id)
            tenant = await load_tenant(uow, tenant_id)
            employee = await load_employee(uow, employee_id)
            leave_type = await load_leave_type(uow, cmd.leave_type_code)
            if cmd.idempotency_key:
                # a replayed submit (agent proposal id or an Idempotency-Key past its stored response) returns the
                # request it already created; a different body under the same key is a mismatch (M7)
                existing = await uow.leave_requests.find_by_idempotency_key(employee_id, cmd.idempotency_key)
                if existing is not None:
                    if (existing.leave_type_id, existing.start_date, existing.end_date) != (
                        leave_type.id,
                        cmd.start_date,
                        cmd.end_date,
                    ):
                        raise ConflictError(
                            "this idempotency key was already used for a different request",
                            problem_type="idempotency-mismatch",
                        )
                    return await self._wf.detail(uow, principal, existing)
            plan = await self._wf.planner.plan(
                uow,
                tenant=tenant,
                employee=employee,
                leave_type=leave_type,
                start=cmd.start_date,
                end=cmd.end_date,
            )
            if not plan.report.valid or plan.routing is None:
                raise LeaveValidationError(plan.report)
            now = self._wf.clock.now()
            request = LeaveRequest(
                id=self._wf.ids.new_id(),
                tenant_id=tenant_id,
                request_number=await uow.leave_requests.next_request_number(cmd.start_date.year),
                employee_id=employee_id,
                leave_type_id=leave_type.id,
                start_date=cmd.start_date,
                end_date=cmd.end_date,
                working_days=Decimal(plan.report.working_days),
                status=RequestStatus.SUBMITTED,
                reason=(cmd.reason or None),
                submitted_at=now,
                decided_at=None,
                cancelled_at=None,
                modification_of_request_id=None,
                idempotency_key=cmd.idempotency_key,
                version=1,
                created_at=now,
                updated_at=now,
            )
            await uow.leave_requests.add(request)
            await self._wf.events.record(
                uow,
                request,
                "submitted",
                from_status=RequestStatus.DRAFT,
                to_status=RequestStatus.SUBMITTED,
                principal=principal,
                at=now,
            )
            await self._wf.route(
                uow,
                tenant=tenant,
                request=request,
                requester=employee,
                leave_type=leave_type,
                routing=plan.routing,
                principal=principal,
                now=now,
            )
            await uow.leave_requests.update(request, expected_version=request.version, bump=False)
            await self._wf.notifications.notify(
                uow,
                [employee_id],
                "request.submitted",
                {
                    "request_id": str(request.id),
                    "request_number": request.request_number,
                    "status": request.status.value,
                },
            )
            await self._wf.audit.record(
                uow,
                principal,
                "leave.request.submitted",
                resource_type="leave_request",
                resource_id=request.id,
                details={
                    "request_number": request.request_number,
                    "leave_type": leave_type.code,
                    "working_days": str(request.working_days),
                    "status": request.status,
                    "auto_acknowledged": plan.routing.auto_acknowledged,
                },
            )
            await uow.commit()
            return await self._wf.detail(uow, principal, request)

    # -- modify ---------------------------------------------------------------------------------------------
    async def validate_modification(
        self, principal: Principal, request_id: UUID, start: date, end: date
    ) -> tuple[LeaveRequest, ValidationReport]:
        """What `modify` would decide, without writing (M7 `prepare_modification`)."""
        tenant_id, employee_id = tenant_user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            request = await uow.leave_requests.get(request_id)
            if request is None or request.employee_id != employee_id:
                raise NotFoundError("request not found")
            require(
                principal,
                Action.MODIFY_OWN_REQUEST,
                ResourceRef(
                    "leave_request", request.id, tenant_id=tenant_id, owner_employee_id=request.employee_id
                ),
            )
            tenant = await load_tenant(uow, tenant_id)
            employee = await load_employee(uow, employee_id)
            leave_type = await uow.leave_types.get(request.leave_type_id)
            assert leave_type is not None
            today = self._wf.clock.now().date()
            credit: dict[int, Decimal] | None = None
            if request.status in PENDING_STATUSES:
                pass
            elif request.status is RequestStatus.APPROVED and request.start_date > today:
                if leave_type.balance_bearing:
                    credit = {
                        p.year: Decimal(p.working_days) for p in await self._wf.periods(uow, tenant, request)
                    }
            else:
                raise InvalidTransition(request.status, RequestEvent.MODIFY)
            plan = await self._wf.planner.plan(
                uow,
                tenant=tenant,
                employee=employee,
                leave_type=leave_type,
                start=start,
                end=end,
                exclude_request_id=request.id,
                credit_days_by_year=credit,
            )
            return request, plan.report

    async def modify(self, principal: Principal, request_id: UUID, cmd: ModifyRequest) -> RequestDetail:
        tenant_id, employee_id = tenant_user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await uow.leave_requests.lock_employee(employee_id)
            request = await uow.leave_requests.get(request_id)
            if request is None or request.employee_id != employee_id:
                raise NotFoundError("request not found")
            require(
                principal,
                Action.MODIFY_OWN_REQUEST,
                ResourceRef(
                    "leave_request", request.id, tenant_id=tenant_id, owner_employee_id=request.employee_id
                ),
            )
            if request.version != cmd.version:
                raise VersionConflict("the request was changed by someone else; reload and retry")
            tenant = await load_tenant(uow, tenant_id)
            employee = await load_employee(uow, employee_id)
            leave_type = await uow.leave_types.get(request.leave_type_id)
            assert leave_type is not None
            now = self._wf.clock.now()
            today = now.date()
            if request.status in PENDING_STATUSES:
                detail = await self._modify_pending(
                    uow, principal, tenant, employee, leave_type, request, cmd, now
                )
            elif request.status is RequestStatus.APPROVED and request.start_date > today:
                detail = await self._modify_approved(
                    uow, principal, tenant, employee, leave_type, request, cmd, now
                )
            else:
                raise InvalidTransition(request.status, RequestEvent.MODIFY)
            await uow.commit()
            return detail

    async def _modify_pending(
        self,
        uow: UnitOfWork,
        principal: Principal,
        tenant: Tenant,
        employee: Employee,
        leave_type: LeaveType,
        request: LeaveRequest,
        cmd: ModifyRequest,
        now: datetime,
    ) -> RequestDetail:
        plan = await self._wf.planner.plan(
            uow,
            tenant=tenant,
            employee=employee,
            leave_type=leave_type,
            start=cmd.start_date,
            end=cmd.end_date,
            exclude_request_id=request.id,
        )
        if not plan.report.valid or plan.routing is None:
            raise LeaveValidationError(plan.report)
        for step in await uow.approval_steps.list_for_request(request.id):
            if step.status is StepStatus.PENDING:
                step.status = StepStatus.CANCELLED
                step.updated_at = now
                await uow.approval_steps.update(step, expected_version=step.version)
        previous = request.status
        payload = {
            "from": {"start_date": request.start_date.isoformat(), "end_date": request.end_date.isoformat()},
            "to": {"start_date": cmd.start_date.isoformat(), "end_date": cmd.end_date.isoformat()},
        }
        request.start_date, request.end_date = cmd.start_date, cmd.end_date
        request.working_days = Decimal(plan.report.working_days)
        if cmd.reason is not UNSET:
            request.reason = cmd.reason or None
        request.status = transition(previous, RequestEvent.MODIFY)
        request.updated_at = now
        await self._wf.events.record(
            uow,
            request,
            "modified",
            from_status=previous,
            to_status=request.status,
            principal=principal,
            payload=payload,
            at=now,
        )
        await self._wf.route(
            uow,
            tenant=tenant,
            request=request,
            requester=employee,
            leave_type=leave_type,
            routing=plan.routing,
            principal=principal,
            now=now,
        )
        await uow.leave_requests.update(request, expected_version=cmd.version)
        await self._wf.audit.record(
            uow,
            principal,
            "leave.request.modified",
            resource_type="leave_request",
            resource_id=request.id,
            details={"request_number": request.request_number, "status": request.status, **payload},
        )
        return await self._wf.detail(uow, principal, request)

    async def _modify_approved(
        self,
        uow: UnitOfWork,
        principal: Principal,
        tenant: Tenant,
        employee: Employee,
        leave_type: LeaveType,
        request: LeaveRequest,
        cmd: ModifyRequest,
        now: datetime,
    ) -> RequestDetail:
        credit = (
            {p.year: Decimal(p.working_days) for p in await self._wf.periods(uow, tenant, request)}
            if leave_type.balance_bearing
            else None
        )
        plan = await self._wf.planner.plan(
            uow,
            tenant=tenant,
            employee=employee,
            leave_type=leave_type,
            start=cmd.start_date,
            end=cmd.end_date,
            exclude_request_id=request.id,
            credit_days_by_year=credit,
        )
        if not plan.report.valid or plan.routing is None:
            raise LeaveValidationError(plan.report)
        modification = LeaveRequest(
            id=self._wf.ids.new_id(),
            tenant_id=request.tenant_id,
            request_number=await uow.leave_requests.next_request_number(cmd.start_date.year),
            employee_id=request.employee_id,
            leave_type_id=request.leave_type_id,
            start_date=cmd.start_date,
            end_date=cmd.end_date,
            working_days=Decimal(plan.report.working_days),
            status=RequestStatus.SUBMITTED,
            reason=(cmd.reason if cmd.reason is not UNSET else request.reason) or None,
            submitted_at=now,
            decided_at=None,
            cancelled_at=None,
            modification_of_request_id=request.id,
            idempotency_key=None,
            version=1,
            created_at=now,
            updated_at=now,
        )
        previous = request.status
        request.status = transition(previous, RequestEvent.MODIFY)
        request.updated_at = now
        await uow.leave_requests.update(request, expected_version=cmd.version)
        await self._wf.events.record(
            uow,
            request,
            "modification_requested",
            from_status=previous,
            to_status=request.status,
            principal=principal,
            payload={"modification_request_id": str(modification.id)},
            at=now,
        )
        await uow.leave_requests.add(modification)
        await self._wf.events.record(
            uow,
            modification,
            "submitted",
            from_status=RequestStatus.DRAFT,
            to_status=RequestStatus.SUBMITTED,
            principal=principal,
            payload={"modification_of_request_id": str(request.id)},
            at=now,
        )
        await self._wf.route(
            uow,
            tenant=tenant,
            request=modification,
            requester=employee,
            leave_type=leave_type,
            routing=plan.routing,
            principal=principal,
            now=now,
        )
        await uow.leave_requests.update(modification, expected_version=modification.version, bump=False)
        if (
            modification.status is RequestStatus.APPROVED
        ):  # auto-acknowledged modification: supersede immediately
            await self.supersede_original(uow, tenant, request, leave_type, principal, now)
        await self._wf.audit.record(
            uow,
            principal,
            "leave.request.modified",
            resource_type="leave_request",
            resource_id=request.id,
            details={
                "request_number": request.request_number,
                "modification_request_id": modification.id,
                "status": modification.status,
            },
        )
        return await self._wf.detail(uow, principal, modification)

    async def supersede_original(
        self,
        uow: UnitOfWork,
        tenant: Tenant,
        original: LeaveRequest,
        leave_type: LeaveType,
        principal: Principal | None,
        now: datetime,
    ) -> None:
        """The modification was approved: the original is cancelled and its deduction reversed (decision 1)."""
        previous = original.status
        original.status = transition(previous, RequestEvent.MODIFICATION_APPROVED)
        original.cancelled_at = now
        original.updated_at = now
        await self._wf.reverse(uow, tenant, original, leave_type, suffix="superseded")
        await self._wf.events.record(
            uow,
            original,
            "superseded",
            from_status=previous,
            to_status=original.status,
            principal=principal,
            at=now,
        )
        await uow.leave_requests.update(original, expected_version=original.version)

    # -- cancel / withdraw ----------------------------------------------------------------------------------
    async def cancel(self, principal: Principal, request_id: UUID, cmd: CancelRequest) -> RequestDetail:
        tenant_id, employee_id = tenant_user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            request = await uow.leave_requests.get(request_id)
            if request is None:
                raise NotFoundError("request not found")
            await uow.leave_requests.lock_employee(request.employee_id)
            is_owner = request.employee_id == employee_id
            if request.status is RequestStatus.ACTIVE:
                require(
                    principal,
                    Action.DECIDE_HR_STEP,
                    ResourceRef("leave_request", request.id, tenant_id=tenant_id),
                )
            elif is_owner:
                require(
                    principal,
                    Action.CANCEL_OWN_REQUEST,
                    ResourceRef(
                        "leave_request",
                        request.id,
                        tenant_id=tenant_id,
                        owner_employee_id=request.employee_id,
                    ),
                )
            else:
                raise NotFoundError("request not found")
            if request.version != cmd.version:
                raise VersionConflict("the request was changed by someone else; reload and retry")
            tenant = await load_tenant(uow, tenant_id)
            leave_type = await uow.leave_types.get(request.leave_type_id)
            assert leave_type is not None
            now = self._wf.clock.now()
            previous = request.status
            reversed_days = ZERO
            if previous is RequestStatus.MODIFICATION_PENDING:
                raise ConflictError(
                    "cancel the pending modification first; the original request is then restored"
                )
            if previous is RequestStatus.SUBMITTED:
                request.status = transition(previous, RequestEvent.WITHDRAW)
                action, event_name = "leave.request.withdrawn", "withdrawn"
            elif previous in PENDING_STATUSES:
                request.status = transition(previous, RequestEvent.CANCEL)
                action, event_name = "leave.request.cancelled", "cancelled"
                await self._cancel_steps(uow, request, now)
            elif previous is RequestStatus.APPROVED:
                request.status = transition(previous, RequestEvent.CANCEL)
                action, event_name = "leave.request.cancelled", "cancelled"
                reversed_days = await self._wf.reverse(uow, tenant, request, leave_type)
            elif previous is RequestStatus.ACTIVE:
                request.status = transition(previous, RequestEvent.CANCEL_ACTIVE)
                action, event_name = "leave.request.cancelled", "cancelled_active"
                reversed_days = await self._wf.reverse(
                    uow, tenant, request, leave_type, from_date=now.date(), suffix="unused"
                )
            else:
                raise InvalidTransition(previous, RequestEvent.CANCEL)
            request.cancelled_at = now
            request.updated_at = now
            await uow.leave_requests.update(request, expected_version=cmd.version)
            await self._wf.events.record(
                uow,
                request,
                event_name,
                from_status=previous,
                to_status=request.status,
                principal=principal,
                payload={"comment": cmd.comment, "reversed_days": str(reversed_days)},
                at=now,
            )
            if request.modification_of_request_id is not None and previous in PENDING_STATUSES:
                await self._restore_original(uow, request.modification_of_request_id, principal, now)
            if previous in DEDUCTED_STATUSES or previous in PENDING_STATUSES:
                watchers = [
                    s.designated_employee_id
                    for s in await uow.approval_steps.list_for_request(request.id)
                    if s.designated_employee_id
                ]
                if not is_owner:
                    watchers.append(request.employee_id)
                if watchers:
                    await self._wf.notifications.notify(
                        uow,
                        watchers,
                        "request.cancelled",
                        {"request_id": str(request.id), "request_number": request.request_number},
                    )
            await self._wf.audit.record(
                uow,
                principal,
                action,
                resource_type="leave_request",
                resource_id=request.id,
                details={
                    "request_number": request.request_number,
                    "from_status": previous,
                    "reversed_days": str(reversed_days),
                    "by_owner": is_owner,
                },
            )
            await uow.commit()
            return await self._wf.detail(uow, principal, request)

    async def _cancel_steps(self, uow: UnitOfWork, request: LeaveRequest, now: datetime) -> None:
        for step in await uow.approval_steps.list_for_request(request.id):
            if step.status is StepStatus.PENDING:
                step.status = StepStatus.CANCELLED
                step.updated_at = now
                await uow.approval_steps.update(step, expected_version=step.version)

    async def _restore_original(
        self, uow: UnitOfWork, original_id: UUID, principal: Principal | None, now: datetime
    ) -> None:
        original = await uow.leave_requests.get(original_id)
        if original is None or original.status is not RequestStatus.MODIFICATION_PENDING:
            return
        previous = original.status
        original.status = transition(previous, RequestEvent.MODIFICATION_REJECTED)
        original.updated_at = now
        await self._wf.events.record(
            uow,
            original,
            "restored",
            from_status=previous,
            to_status=original.status,
            principal=principal,
            at=now,
        )
        await uow.leave_requests.update(original, expected_version=original.version)
