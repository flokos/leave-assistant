"""Approval decisions (FR-APPR-03…07, doc 09 §2.4): designated approvers, HR fallback, segregation of duties."""

from __future__ import annotations

from uuid import UUID

from leave_assistant.application.authz.policy import Action, ResourceRef, authorize, require
from leave_assistant.application.errors import InvalidInputError, NotFoundError
from leave_assistant.application.leave.commands import Decide
from leave_assistant.application.leave.common import load_employee, load_tenant, tenant_user
from leave_assistant.application.leave.errors import (
    ApprovalNotPermitted,
    SegregationOfDuties,
    StepNotPending,
    VersionConflict,
)
from leave_assistant.application.leave.requests import LeaveRequestService, RequestWorkflow, supporting_count
from leave_assistant.application.leave.views import PendingItem, RequestDetail, reason_visible
from leave_assistant.application.ports import UnitOfWork, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.request import ApprovalStep, LeaveRequest, StepStatus
from leave_assistant.domain.leave.state_machine import RequestEvent, RequestStatus, transition
from leave_assistant.domain.leave.types import StepRole
from leave_assistant.domain.tenancy import Principal, Role


class ApprovalService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, workflow: RequestWorkflow, requests: LeaveRequestService
    ) -> None:
        self._uow = uow_factory
        self._wf = workflow
        self._requests = requests

    async def _item(
        self,
        uow: UnitOfWork,
        principal: Principal,
        step: ApprovalStep,
        request: LeaveRequest,
        *,
        fallback: bool,
    ) -> PendingItem:
        requester = await load_employee(uow, request.employee_id)
        leave_type = await uow.leave_types.get(request.leave_type_id)
        assert leave_type is not None
        return PendingItem(
            step,
            request,
            requester,
            leave_type,
            fallback,
            reason_visible(principal, request, leave_type),
            supporting_count=await supporting_count(uow, principal, request),
        )

    @staticmethod
    def _is_current(step: ApprovalStep, steps: list[ApprovalStep]) -> bool:
        pending = [s for s in steps if s.status is StepStatus.PENDING]
        return bool(pending) and min(pending, key=lambda s: s.step_no).id == step.id

    async def pending(self, principal: Principal) -> list[PendingItem]:
        tenant_id, employee_id = tenant_user(principal)
        if not principal.has_any(Role.MANAGER, Role.HR_ADMIN):
            require(principal, Action.DECIDE_HR_STEP)  # raises a uniform denial for other roles
        items: list[PendingItem] = []
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            tenant = await load_tenant(uow, tenant_id)
            candidates: list[tuple[ApprovalStep, bool]] = []
            if Role.MANAGER in principal.roles:
                candidates += [
                    (s, False)
                    for s in await uow.approval_steps.list_pending(designated_employee_id=employee_id)
                ]
            if Role.HR_ADMIN in principal.roles:
                candidates += [(s, False) for s in await uow.approval_steps.list_pending(role=StepRole.HR)]
                if tenant.settings.allow_hr_fallback:
                    candidates += [
                        (s, True)
                        for s in await uow.approval_steps.list_pending(role=StepRole.MANAGER)
                        if s.designated_employee_id != employee_id
                    ]
            seen: set[UUID] = set()
            for step, fallback in candidates:
                if step.id in seen:
                    continue
                seen.add(step.id)
                request = await uow.leave_requests.get(step.request_id)
                if request is None or request.employee_id == employee_id:
                    continue  # never list one's own request as decidable
                if not self._is_current(step, await uow.approval_steps.list_for_request(request.id)):
                    continue
                items.append(await self._item(uow, principal, step, request, fallback=fallback))
        return sorted(items, key=lambda i: (i.request.submitted_at or i.request.created_at, i.step.step_no))

    async def get_step(self, principal: Principal, step_id: UUID) -> PendingItem:
        tenant_id, employee_id = tenant_user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            step = await uow.approval_steps.get(step_id)
            request = await uow.leave_requests.get(step.request_id) if step else None
            if step is None or request is None:
                raise NotFoundError("approval step not found")
            if step.designated_employee_id != employee_id and Role.HR_ADMIN not in principal.roles:
                raise ApprovalNotPermitted(
                    "not an approver of this step",
                    action=Action.DECIDE_MANAGER_STEP.value,
                    resource=f"approval_step:{step.id}",
                )
            return await self._item(
                uow,
                principal,
                step,
                request,
                fallback=step.approver_role is StepRole.MANAGER
                and step.designated_employee_id != employee_id,
            )

    async def approve(self, principal: Principal, step_id: UUID, cmd: Decide) -> RequestDetail:
        return await self._decide(principal, step_id, cmd, approve=True)

    async def reject(self, principal: Principal, step_id: UUID, cmd: Decide) -> RequestDetail:
        if not (cmd.comment or "").strip():
            raise InvalidInputError(
                "a comment is required when rejecting",
                errors=[{"loc": ["body", "comment"], "msg": "required"}],
            )
        return await self._decide(principal, step_id, cmd, approve=False)

    async def _decide(
        self, principal: Principal, step_id: UUID, cmd: Decide, *, approve: bool
    ) -> RequestDetail:
        tenant_id, employee_id = tenant_user(principal)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            step = await uow.approval_steps.get(step_id)
            request = await uow.leave_requests.get(step.request_id) if step else None
            if step is None or request is None:
                raise NotFoundError("approval step not found")
            await uow.leave_requests.lock_employee(request.employee_id)
            steps = await uow.approval_steps.list_for_request(request.id)
            if step.status is not StepStatus.PENDING or not self._is_current(step, steps):
                raise StepNotPending("this step is not awaiting a decision")
            if step.version != cmd.version:
                raise VersionConflict("the step was changed by someone else; reload and retry")
            if request.employee_id == employee_id:
                raise ApprovalNotPermitted(
                    "nobody decides a step on their own request",
                    action="approval.decide",
                    resource=f"approval_step:{step.id}",
                )
            tenant = await load_tenant(uow, tenant_id)
            requester = await load_employee(uow, request.employee_id)
            fallback = False
            if step.approver_role is StepRole.MANAGER:
                if step.designated_employee_id == employee_id and Role.MANAGER in principal.roles:
                    # designation is authoritative (manager override may sit in another department)
                    require(
                        principal,
                        Action.DECIDE_MANAGER_STEP,
                        ResourceRef(
                            "approval_step",
                            step.id,
                            tenant_id=tenant_id,
                            owner_employee_id=request.employee_id,
                            department_id=principal.department_id,
                            department_manager_employee_id=employee_id,
                        ),
                    )
                elif Role.HR_ADMIN in principal.roles:
                    if not tenant.settings.allow_hr_fallback:
                        raise ApprovalNotPermitted(
                            "HR fallback on manager steps is disabled for this company",
                            action=Action.DECIDE_MANAGER_STEP.value,
                            resource=f"approval_step:{step.id}",
                        )
                    require(
                        principal,
                        Action.DECIDE_MANAGER_STEP,
                        ResourceRef(
                            "approval_step",
                            step.id,
                            tenant_id=tenant_id,
                            owner_employee_id=request.employee_id,
                        ),
                    )
                    fallback = True
                else:
                    raise ApprovalNotPermitted(
                        "not the designated approver of this step",
                        action=Action.DECIDE_MANAGER_STEP.value,
                        resource=f"approval_step:{step.id}",
                    )
            else:
                decision = authorize(
                    principal,
                    Action.DECIDE_HR_STEP,
                    ResourceRef(
                        "approval_step", step.id, tenant_id=tenant_id, owner_employee_id=request.employee_id
                    ),
                )
                if not decision.allowed:
                    raise ApprovalNotPermitted(
                        decision.reason,
                        action=Action.DECIDE_HR_STEP.value,
                        resource=f"approval_step:{step.id}",
                    )
                if any(s.decided_by_employee_id == employee_id for s in steps if s.id != step.id):
                    raise SegregationOfDuties(
                        "the same person cannot decide both steps of a request",
                        action=Action.DECIDE_HR_STEP.value,
                        resource=f"approval_step:{step.id}",
                    )
            leave_type = await uow.leave_types.get(request.leave_type_id)
            assert leave_type is not None
            now = self._wf.clock.now()
            step.status = StepStatus.APPROVED if approve else StepStatus.REJECTED
            step.decided_by_employee_id = employee_id
            step.decided_at = now
            step.comment = (cmd.comment or "").strip() or None
            step.via = cmd.via
            step.fallback = fallback
            step.updated_at = now
            await uow.approval_steps.update(step, expected_version=cmd.version)
            previous = request.status
            params = {
                "request_id": str(request.id),
                "request_number": request.request_number,
                "leave_type": leave_type.code,
                "step_no": step.step_no,
                "comment": step.comment,
            }
            if approve:
                remaining = [s for s in steps if s.status is StepStatus.PENDING and s.step_no > step.step_no]
                if remaining:
                    request.status = transition(previous, RequestEvent.MANAGER_APPROVED)
                    await self._wf.events.record(
                        uow,
                        request,
                        "step_approved",
                        from_status=previous,
                        to_status=request.status,
                        principal=principal,
                        payload={"step_no": step.step_no, "fallback": fallback},
                        at=now,
                    )
                    await self._wf.notify_step(
                        uow,
                        min(remaining, key=lambda s: s.step_no),
                        {k: v for k, v in params.items() if k != "comment"},
                    )
                else:
                    request.status = transition(previous, RequestEvent.FINAL_APPROVE)
                    request.decided_at = now
                    await self._wf.events.record(
                        uow,
                        request,
                        "approved",
                        from_status=previous,
                        to_status=request.status,
                        principal=principal,
                        payload={"step_no": step.step_no, "fallback": fallback},
                        at=now,
                    )
                    await self._wf.deduct(uow, tenant, request, leave_type)
                    await self._wf.notifications.notify(
                        uow, [request.employee_id], "request.approved", params
                    )
                    if request.modification_of_request_id is not None:
                        original = await uow.leave_requests.get(request.modification_of_request_id)
                        if original is not None and original.status is RequestStatus.MODIFICATION_PENDING:
                            await self._requests.supersede_original(
                                uow, tenant, original, leave_type, principal, now
                            )
            else:
                request.status = transition(previous, RequestEvent.REJECT)
                request.decided_at = now
                for other in steps:
                    if other.id != step.id and other.status is StepStatus.PENDING:
                        other.status = StepStatus.CANCELLED
                        other.updated_at = now
                        await uow.approval_steps.update(other, expected_version=other.version)
                await self._wf.events.record(
                    uow,
                    request,
                    "rejected",
                    from_status=previous,
                    to_status=request.status,
                    principal=principal,
                    payload={"step_no": step.step_no, "comment": step.comment, "fallback": fallback},
                    at=now,
                )
                await self._wf.notifications.notify(uow, [request.employee_id], "request.rejected", params)
                if request.modification_of_request_id is not None:
                    await self._requests._restore_original(
                        uow, request.modification_of_request_id, principal, now
                    )
            request.updated_at = now
            await uow.leave_requests.update(request, expected_version=request.version)
            await self._wf.audit.record(
                uow,
                principal,
                "leave.request.approved" if approve else "leave.request.rejected",
                resource_type="leave_request",
                resource_id=request.id,
                details={
                    "request_number": request.request_number,
                    "step_no": step.step_no,
                    "approver_role": step.approver_role,
                    "fallback": fallback,
                    "via": cmd.via,
                    "status": request.status,
                    "requester_id": requester.id,
                },
            )
            await uow.commit()
            return await self._wf.detail(uow, principal, request)
