"""Leave request and approval services with the in-memory world (FR-LEAVE-04…08, FR-APPR-03…07, ADR-0028)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import (
    AuthorizationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
)
from leave_assistant.application.leave.commands import (
    Adjustment,
    CancelRequest,
    Decide,
    ModifyRequest,
    SubmitRequest,
)
from leave_assistant.application.leave.errors import (
    ApprovalNotPermitted,
    LeaveValidationError,
    SegregationOfDuties,
    StepNotPending,
    VersionConflict,
)
from leave_assistant.domain.leave.ledger import EntryType
from leave_assistant.domain.leave.request import StepStatus
from leave_assistant.domain.leave.state_machine import InvalidTransition, RequestStatus
from leave_assistant.domain.leave.types import StepRole
from leave_assistant.domain.tenancy import Role, TenantSettings
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World

DEC_14, DEC_18 = date(2026, 12, 14), date(2026, 12, 18)  # Mon–Fri, 5 working days (Greek calendar)


def submit(leave_type: str, start: date, end: date, **kw: object) -> SubmitRequest:
    return SubmitRequest(leave_type, start, end, **kw)  # type: ignore[arg-type]


def balance(
    services_store: MemoryStore, employee_id, leave_type_id, year: int = 2026
) -> tuple[Decimal, Decimal]:  # type: ignore[no-untyped-def]
    from leave_assistant.domain.leave.state_machine import PENDING_STATUSES

    total = sum(
        (
            e.quantity_days
            for e in services_store.ledger
            if e.employee_id == employee_id and e.leave_type_id == leave_type_id and e.period_year == year
        ),
        Decimal("0"),
    )
    pending = sum(
        (
            r.working_days
            for r in services_store.leave_requests.values()
            if r.employee_id == employee_id
            and r.leave_type_id == leave_type_id
            and r.status in PENDING_STATUSES
        ),
        Decimal("0"),
    )
    return total, pending


async def test_validate_reports_chain_and_balance(services: Services, leave_world: World) -> None:
    employee = principal_for(leave_world.a.employees["employee"])
    report = await services.leave_requests.validate(employee, submit("annual", DEC_14, date(2026, 12, 15)))
    assert report.valid and report.working_days == 2 and report.approval_chain == (StepRole.MANAGER,)
    assert report.balance is not None and report.balance.after_days == Decimal("18")


async def test_submit_routes_to_department_manager(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester = leave_world.a.employees["employee"]
    manager = leave_world.a.employees["manager"]
    detail = await services.leave_requests.submit(
        principal_for(requester),
        submit("annual", DEC_14, date(2026, 12, 16), reason="trip", idempotency_key="k1"),
    )
    request = detail.request
    assert request.status is RequestStatus.PENDING_MANAGER and request.request_number == "LR-2026-000001"
    assert request.working_days == 3 and request.idempotency_key == "k1" and request.version == 1
    assert [(s.step_no, s.approver_role, s.designated_employee_id, s.status) for s in detail.steps] == [
        (1, StepRole.MANAGER, manager.id, StepStatus.PENDING)
    ]
    assert [e.event_type for e in detail.events] == ["submitted", "routed"]
    kinds = {(n.recipient_employee_id, n.type) for n in store.notifications.values()}
    assert kinds == {(requester.id, "request.submitted"), (manager.id, "approval.pending")}
    assert len(store.integration_events) == 2 and all(
        e.type == "notification.created" for e in store.integration_events
    )
    assert (
        store.audit[-1].action == "leave.request.submitted"
        and store.audit[-1].details["status"] == "PENDING_MANAGER"
    )
    assert balance(store, requester.id, leave_world.leave_types["a"]["annual"].id) == (
        Decimal("20"),
        Decimal("3"),
    )  # type: ignore[attr-defined]


async def test_two_step_approval_posts_deduction_on_final_approval(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, manager, hr = (leave_world.a.employees[k] for k in ("employee", "manager", "hr_admin"))
    detail = await services.leave_requests.submit(
        principal_for(requester), submit("annual", date(2026, 11, 16), date(2026, 11, 24))
    )  # 7 working days
    assert detail.request.status is RequestStatus.PENDING_MANAGER and [
        s.approver_role for s in detail.steps
    ] == [StepRole.MANAGER, StepRole.HR]
    pending_for_manager = await services.approvals.pending(principal_for(manager))
    assert [i.request.id for i in pending_for_manager] == [detail.request.id] and not pending_for_manager[
        0
    ].fallback
    assert await services.approvals.pending(principal_for(hr)) == [] or all(
        i.fallback for i in await services.approvals.pending(principal_for(hr))
    )

    after_manager = await services.approvals.approve(
        principal_for(manager), detail.steps[0].id, Decide(version=1, comment="ok")
    )
    assert after_manager.request.status is RequestStatus.PENDING_HR
    assert (
        after_manager.steps[0].status is StepStatus.APPROVED
        and after_manager.steps[0].decided_by_employee_id == manager.id
    )
    assert any(
        n.recipient_employee_id == hr.id and n.type == "approval.pending"
        for n in store.notifications.values()
    )
    assert balance(store, requester.id, leave_world.leave_types["a"]["annual"].id) == (
        Decimal("20"),
        Decimal("7"),
    )  # type: ignore[attr-defined]

    hr_pending = await services.approvals.pending(principal_for(hr))
    assert [i.step.id for i in hr_pending] == [after_manager.steps[1].id] and not hr_pending[0].fallback
    final = await services.approvals.approve(principal_for(hr), after_manager.steps[1].id, Decide(version=1))
    assert final.request.status is RequestStatus.APPROVED and final.request.decided_at is not None
    deductions = [e for e in store.ledger if e.entry_type is EntryType.DEDUCTION]
    assert [(e.quantity_days, e.period_year, e.request_id) for e in deductions] == [
        (Decimal("-7"), 2026, final.request.id)
    ]
    assert balance(store, requester.id, leave_world.leave_types["a"]["annual"].id) == (
        Decimal("13"),
        Decimal("0"),
    )  # type: ignore[attr-defined]
    assert [e.event_type for e in final.events] == ["submitted", "routed", "step_approved", "approved"]
    assert any(
        n.recipient_employee_id == requester.id and n.type == "request.approved"
        for n in store.notifications.values()
    )
    assert [a.action for a in store.audit if a.action.startswith("leave.")] == [
        "leave.request.submitted",
        "leave.request.approved",
        "leave.request.approved",
    ]


async def test_sick_leave_auto_acknowledged(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, manager, hr = (leave_world.a.employees[k] for k in ("employee", "manager", "hr_admin"))
    detail = await services.leave_requests.submit(
        principal_for(requester), submit("sick", date(2026, 11, 2), date(2026, 11, 3), reason="flu")
    )
    assert (
        detail.request.status is RequestStatus.APPROVED
        and detail.steps == []
        and detail.request.decided_at is not None
    )
    assert [e.event_type for e in detail.events] == ["submitted", "auto_acknowledged"]
    sick = leave_world.leave_types["a"]["sick"].id  # type: ignore[attr-defined]
    assert balance(store, requester.id, sick) == (Decimal("8"), Decimal("0"))
    kinds = {(n.recipient_employee_id, n.type) for n in store.notifications.values()}
    assert {
        (requester.id, "request.approved"),
        (manager.id, "sick.auto_acknowledged"),
        (hr.id, "sick.auto_acknowledged"),
    } <= kinds
    assert all(
        "flu" not in str(n.params) for n in store.notifications.values() if n.type == "sick.auto_acknowledged"
    )


async def test_long_or_documented_sick_leave_goes_to_hr_only(services: Services, leave_world: World) -> None:
    requester = leave_world.a.employees["employee"]
    report = await services.leave_requests.validate(
        principal_for(requester), submit("sick", date(2026, 11, 2), date(2026, 11, 6))
    )
    assert report.approval_chain == (StepRole.HR,) and [w.code for w in report.warnings] == [
        "leave/document-required"
    ]
    detail = await services.leave_requests.submit(
        principal_for(requester), submit("sick", date(2026, 11, 2), date(2026, 11, 6))
    )
    assert detail.request.status is RequestStatus.PENDING_HR and [s.approver_role for s in detail.steps] == [
        StepRole.HR
    ]


async def test_sick_leave_over_cap_needs_hr_review(services: Services, leave_world: World) -> None:
    requester, hr = leave_world.a.employees["employee"], leave_world.a.employees["hr_admin"]
    await services.ledger.adjust(
        principal_for(hr), Adjustment(requester.id, "sick", Decimal("-9"), "correction")
    )
    detail = await services.leave_requests.submit(
        principal_for(requester), submit("sick", date(2026, 11, 2), date(2026, 11, 3))
    )
    assert (
        detail.request.status is RequestStatus.PENDING_HR
    )  # 2 days requested, 1 available → no auto-acknowledgement


async def test_managers_own_request_routes_to_hr_or_override(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    manager = leave_world.a.employees["manager"]
    detail = await services.leave_requests.submit(
        principal_for(manager), submit("annual", DEC_14, date(2026, 12, 15))
    )
    assert detail.request.status is RequestStatus.PENDING_HR and [s.approver_role for s in detail.steps] == [
        StepRole.HR
    ]
    other = leave_world.a.employees["employee2"]
    store.employees[other.id].roles = frozenset({Role.MANAGER, Role.EMPLOYEE})
    store.employees[manager.id].manager_override_employee_id = other.id
    second = await services.leave_requests.submit(
        principal_for(store.employees[manager.id]), submit("annual", date(2026, 12, 21), date(2026, 12, 22))
    )
    assert [(s.approver_role, s.designated_employee_id) for s in second.steps] == [
        (StepRole.MANAGER, other.id)
    ]


@pytest.mark.parametrize(
    ("cmd", "code"),
    [
        (submit("annual", date(2026, 11, 2), date(2026, 11, 3)), "leave/retroactive-not-allowed"),
        (submit("annual", date(2026, 12, 18), date(2026, 12, 14)), "leave/invalid-dates"),
        (
            submit("annual", date(2026, 11, 9), date(2026, 12, 11)),
            "leave/insufficient-balance",
        ),  # 25 working days > 20
        (submit("annual", date(2026, 12, 26), date(2026, 12, 27)), "leave/invalid-dates"),
    ],
)
async def test_submit_rejects_invalid_requests(
    services: Services, leave_world: World, cmd: SubmitRequest, code: str, store: MemoryStore
) -> None:
    requester = principal_for(leave_world.a.employees["employee"])
    with pytest.raises(LeaveValidationError) as excinfo:
        await services.leave_requests.submit(requester, cmd)
    assert excinfo.value.problem_type == code and excinfo.value.errors[0]["code"] == code
    assert not store.leave_requests


async def test_overlap_is_rejected_and_unknown_type_fails(services: Services, leave_world: World) -> None:
    requester = principal_for(leave_world.a.employees["employee"])
    await services.leave_requests.submit(requester, submit("annual", DEC_14, DEC_18))
    with pytest.raises(LeaveValidationError, match="overlap"):
        await services.leave_requests.submit(
            requester, submit("unpaid", date(2026, 12, 16), date(2026, 12, 21))
        )
    with pytest.raises(InvalidInputError):  # unknown leave type
        await services.leave_requests.submit(requester, submit("sabbatical", DEC_14, DEC_18))


async def test_modify_pending_request_restarts_the_chain(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester = principal_for(leave_world.a.employees["employee"])
    detail = await services.leave_requests.submit(requester, submit("annual", DEC_14, date(2026, 12, 15)))
    with pytest.raises(VersionConflict):
        await services.leave_requests.modify(
            requester, detail.request.id, ModifyRequest(date(2026, 11, 16), date(2026, 11, 24), version=7)
        )
    modified = await services.leave_requests.modify(
        requester,
        detail.request.id,
        ModifyRequest(date(2026, 11, 16), date(2026, 11, 24), version=1, reason="longer"),
    )
    assert (
        modified.request.version == 2
        and modified.request.working_days == 7
        and modified.request.reason == "longer"
    )
    assert modified.request.status is RequestStatus.PENDING_MANAGER
    statuses = [(s.step_no, s.approver_role, s.status) for s in modified.steps]
    assert statuses == [
        (1, StepRole.MANAGER, StepStatus.CANCELLED),
        (1, StepRole.MANAGER, StepStatus.PENDING),
        (2, StepRole.HR, StepStatus.PENDING),
    ]
    assert [e.event_type for e in modified.events] == ["submitted", "routed", "modified", "routed"]
    assert store.audit[-1].action == "leave.request.modified"


async def test_modify_approved_request_supersedes_on_approval(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, manager = leave_world.a.employees["employee"], leave_world.a.employees["manager"]
    annual = leave_world.leave_types["a"]["annual"].id  # type: ignore[attr-defined]
    detail = await services.leave_requests.submit(principal_for(requester), submit("annual", DEC_14, DEC_18))
    approved = await services.approvals.approve(principal_for(manager), detail.steps[0].id, Decide(version=1))
    assert approved.request.status is RequestStatus.APPROVED and balance(store, requester.id, annual) == (
        Decimal("15"),
        Decimal("0"),
    )

    modification = await services.leave_requests.modify(
        principal_for(requester), approved.request.id, ModifyRequest(DEC_14, date(2026, 12, 16), version=2)
    )
    assert (
        modification.request.modification_of_request_id == approved.request.id
        and modification.request.status is RequestStatus.PENDING_MANAGER
    )
    original = store.leave_requests[approved.request.id]
    assert original.status is RequestStatus.MODIFICATION_PENDING and original.version == 3
    assert balance(store, requester.id, annual) == (
        Decimal("15"),
        Decimal("3"),
    )  # original still deducted, modification pending

    final = await services.approvals.approve(
        principal_for(manager), modification.steps[0].id, Decide(version=1)
    )
    assert final.request.status is RequestStatus.APPROVED
    original = store.leave_requests[approved.request.id]
    assert original.status is RequestStatus.CANCELLED and original.cancelled_at is not None
    types = sorted(
        (e.entry_type.value, str(e.quantity_days))
        for e in store.ledger
        if e.request_id in {original.id, final.request.id}
    )
    assert types == [("DEDUCTION", "-3"), ("DEDUCTION", "-5"), ("REVERSAL", "5")]
    assert balance(store, requester.id, annual) == (Decimal("17"), Decimal("0"))
    assert [e.event_type for e in await _events(services, requester, original.id)] == [
        "submitted",
        "routed",
        "approved",
        "modification_requested",
        "superseded",
    ]


async def _events(services: Services, employee, request_id):  # type: ignore[no-untyped-def]
    return (await services.leave_requests.get(principal_for(employee), request_id)).events


async def test_rejecting_or_cancelling_a_modification_restores_the_original(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, manager = leave_world.a.employees["employee"], leave_world.a.employees["manager"]
    detail = await services.leave_requests.submit(principal_for(requester), submit("annual", DEC_14, DEC_18))
    approved = await services.approvals.approve(principal_for(manager), detail.steps[0].id, Decide(version=1))
    modification = await services.leave_requests.modify(
        principal_for(requester), approved.request.id, ModifyRequest(DEC_14, date(2026, 12, 16), version=2)
    )
    rejected = await services.approvals.reject(
        principal_for(manager), modification.steps[0].id, Decide(version=1, comment="no")
    )
    assert rejected.request.status is RequestStatus.REJECTED
    assert store.leave_requests[approved.request.id].status is RequestStatus.APPROVED
    # cancelling a pending modification also restores the original
    modification2 = await services.leave_requests.modify(
        principal_for(requester), approved.request.id, ModifyRequest(DEC_14, date(2026, 12, 17), version=4)
    )
    assert store.leave_requests[approved.request.id].status is RequestStatus.MODIFICATION_PENDING
    cancelled = await services.leave_requests.cancel(
        principal_for(requester), modification2.request.id, CancelRequest(version=1)
    )
    assert cancelled.request.status is RequestStatus.CANCELLED
    assert store.leave_requests[approved.request.id].status is RequestStatus.APPROVED
    with pytest.raises(ConflictError):  # an original in MODIFICATION_PENDING cannot be cancelled directly
        modification3 = await services.leave_requests.modify(
            principal_for(requester),
            approved.request.id,
            ModifyRequest(DEC_14, date(2026, 12, 15), version=6),
        )
        await services.leave_requests.cancel(
            principal_for(requester), approved.request.id, CancelRequest(version=7)
        )
        del modification3


async def test_cancel_and_withdraw_paths(services: Services, leave_world: World, store: MemoryStore) -> None:
    requester, manager = leave_world.a.employees["employee"], leave_world.a.employees["manager"]
    annual = leave_world.leave_types["a"]["annual"].id  # type: ignore[attr-defined]
    pending = await services.leave_requests.submit(
        principal_for(requester), submit("annual", DEC_14, date(2026, 12, 15))
    )
    cancelled = await services.leave_requests.cancel(
        principal_for(requester), pending.request.id, CancelRequest(version=1, comment="changed plans")
    )
    assert cancelled.request.status is RequestStatus.CANCELLED and all(
        s.status is StepStatus.CANCELLED for s in cancelled.steps
    )
    assert not [e for e in store.ledger if e.entry_type is EntryType.REVERSAL]
    assert any(
        n.recipient_employee_id == manager.id and n.type == "request.cancelled"
        for n in store.notifications.values()
    )

    approved = await services.leave_requests.submit(
        principal_for(requester), submit("annual", date(2026, 12, 21), date(2026, 12, 23))
    )
    approved = await services.approvals.approve(
        principal_for(manager), approved.steps[0].id, Decide(version=1)
    )
    assert balance(store, requester.id, annual) == (Decimal("17"), Decimal("0"))
    cancelled2 = await services.leave_requests.cancel(
        principal_for(requester), approved.request.id, CancelRequest(version=2)
    )
    assert cancelled2.request.status is RequestStatus.CANCELLED and balance(store, requester.id, annual) == (
        Decimal("20"),
        Decimal("0"),
    )
    with pytest.raises(InvalidTransition):
        await services.leave_requests.cancel(
            principal_for(requester), approved.request.id, CancelRequest(version=3)
        )
    with pytest.raises(NotFoundError):  # another employee's request is invisible
        await services.leave_requests.cancel(
            principal_for(leave_world.a.employees["employee2"]), approved.request.id, CancelRequest(version=3)
        )


async def test_active_request_cancelled_by_hr_with_partial_reversal(
    services: Services, leave_world: World, store: MemoryStore, clock
) -> None:  # type: ignore[no-untyped-def]
    requester, manager, hr = (leave_world.a.employees[k] for k in ("employee", "manager", "hr_admin"))
    annual = leave_world.leave_types["a"]["annual"].id  # type: ignore[attr-defined]
    detail = await services.leave_requests.submit(
        principal_for(requester), submit("annual", date(2026, 11, 9), date(2026, 11, 13))
    )  # Mon–Fri
    approved = await services.approvals.approve(principal_for(manager), detail.steps[0].id, Decide(version=1))
    from datetime import UTC, datetime

    clock.current = datetime(
        2026, 11, 11, 9, 0, tzinfo=UTC
    )  # Wednesday: two days used, three remaining incl. today
    tenant = store.tenants[leave_world.a.tenant.id]
    report = await services.rollover.run_for_tenant(tenant, date(2026, 11, 11))
    assert report.started == 1 and store.leave_requests[approved.request.id].status is RequestStatus.ACTIVE
    with pytest.raises(AuthorizationError):
        await services.leave_requests.cancel(
            principal_for(requester), approved.request.id, CancelRequest(version=3)
        )
    cancelled = await services.leave_requests.cancel(
        principal_for(hr), approved.request.id, CancelRequest(version=3, comment="returned early")
    )
    assert cancelled.request.status is RequestStatus.CANCELLED
    reversal = [e for e in store.ledger if e.entry_type is EntryType.REVERSAL]
    assert [e.quantity_days for e in reversal] == [Decimal("3")] and balance(store, requester.id, annual) == (
        Decimal("18"),
        Decimal("0"),
    )


async def test_approval_permissions_fallback_and_segregation(
    services: Services, leave_world: World, store: MemoryStore
) -> None:
    requester, manager, hr, admin = (
        leave_world.a.employees[k] for k in ("employee", "manager", "hr_admin", "company_admin")
    )
    detail = await services.leave_requests.submit(
        principal_for(requester), submit("annual", date(2026, 11, 16), date(2026, 11, 24))
    )  # manager → HR
    manager_step, hr_step = detail.steps
    with pytest.raises(ApprovalNotPermitted):
        await services.approvals.approve(principal_for(admin), manager_step.id, Decide(version=1))
    with pytest.raises(ApprovalNotPermitted):  # employee2 is not the designated manager
        await services.approvals.approve(
            principal_for(leave_world.a.employees["employee2"]), manager_step.id, Decide(version=1)
        )
    with pytest.raises(StepNotPending):  # the HR step is not current yet
        await services.approvals.approve(principal_for(hr), hr_step.id, Decide(version=1))
    with pytest.raises(VersionConflict):
        await services.approvals.approve(principal_for(manager), manager_step.id, Decide(version=2))
    with pytest.raises(
        ApprovalNotPermitted
    ):  # own request (manager submits, HR fallback is not self-approval either)
        own = await services.leave_requests.submit(
            principal_for(hr), submit("annual", date(2026, 12, 21), date(2026, 12, 22))
        )
        await services.approvals.approve(principal_for(hr), own.steps[0].id, Decide(version=1))

    # HR fallback on the manager step is recorded, then the same HR admin cannot decide the HR step
    after = await services.approvals.approve(
        principal_for(hr), manager_step.id, Decide(version=1, comment="manager on leave")
    )
    assert (
        after.steps[0].fallback
        and after.steps[0].decided_by_employee_id == hr.id
        and after.request.status is RequestStatus.PENDING_HR
    )
    assert store.audit[-1].details["fallback"] is True
    with pytest.raises(SegregationOfDuties):
        await services.approvals.approve(principal_for(hr), hr_step.id, Decide(version=1))
    with pytest.raises(Exception, match="comment"):
        await services.approvals.reject(principal_for(hr), hr_step.id, Decide(version=1))

    # fallback can be disabled per tenant
    tenant = store.tenants[leave_world.a.tenant.id]
    tenant.settings = TenantSettings(allow_hr_fallback=False)
    detail2 = await services.leave_requests.submit(
        principal_for(requester), submit("annual", date(2026, 12, 7), date(2026, 12, 8))
    )
    with pytest.raises(ApprovalNotPermitted, match="fallback"):
        await services.approvals.approve(principal_for(hr), detail2.steps[0].id, Decide(version=1))


async def test_request_visibility(services: Services, leave_world: World) -> None:
    requester, manager, hr, admin, other = (
        leave_world.a.employees[k] for k in ("employee", "manager", "hr_admin", "company_admin", "employee2")
    )
    detail = await services.leave_requests.submit(
        principal_for(requester), submit("sick", date(2026, 11, 2), date(2026, 11, 6), reason="surgery")
    )
    assert (
        await services.leave_requests.get(principal_for(requester), detail.request.id)
    ).reason == "surgery"
    assert (await services.leave_requests.get(principal_for(hr), detail.request.id)).reason == "surgery"
    manager_view = await services.leave_requests.get(
        principal_for(manager), detail.request.id
    )  # department manager
    assert manager_view.reason is None and manager_view.leave_type.code == "sick"
    with pytest.raises(AuthorizationError):
        await services.leave_requests.get(principal_for(other), detail.request.id)
    admin_view = await services.leave_requests.get(principal_for(admin), detail.request.id)  # absence only
    assert admin_view.reason is None and not admin_view.leave_type_visible
    with pytest.raises(NotFoundError):
        await services.leave_requests.get(
            principal_for(leave_world.b.employees["hr_admin"]), detail.request.id
        )
    page = await services.leave_requests.list(
        principal_for(requester), statuses=None, date_from=None, date_to=None, limit=None, cursor=None
    )
    assert [r.id for r in page.items] == [detail.request.id]
    with pytest.raises(NotFoundError):
        await services.leave_requests.get(principal_for(requester), uuid4())
