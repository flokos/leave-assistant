"""ADR-0006 / ADR-0028: rule selection table (doc 01 §6.4) and step materialisation (routing)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from leave_assistant.domain.leave.rules import StepRole, route, select_rule
from leave_assistant.domain.leave.types import ApprovalRule, LeaveType

NOW = datetime(2026, 11, 5, tzinfo=UTC)
TENANT = UUID(int=9)


def leave_type(code: str, **kw: object) -> LeaveType:
    base = dict(
        id=uuid4(),
        tenant_id=TENANT,
        code=code,
        name_el=code,
        name_en=code,
        balance_bearing=code != "unpaid",
        allow_retroactive_days=30 if code == "sick" else 0,
        requires_document_above_days=3 if code == "sick" else None,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(kw)
    return LeaveType(**base)  # type: ignore[arg-type]


ANNUAL, SICK, UNPAID = leave_type("annual"), leave_type("sick"), leave_type("unpaid")


def rule(**kw: object) -> ApprovalRule:
    base = dict(
        id=uuid4(),
        tenant_id=TENANT,
        leave_type_id=None,
        min_working_days=Decimal("0"),
        max_working_days=None,
        requires_document=False,
        steps=(StepRole.MANAGER,),
        auto_acknowledge=False,
        priority=100,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(kw)
    return ApprovalRule(**base)  # type: ignore[arg-type]


SEED_RULES = [
    rule(leave_type_id=SICK.id, max_working_days=Decimal("3"), steps=(), auto_acknowledge=True, priority=10),
    rule(
        leave_type_id=SICK.id,
        min_working_days=Decimal("3.01"),
        steps=(StepRole.HR,),
        requires_document=True,
        priority=20,
    ),
    rule(max_working_days=Decimal("5"), steps=(StepRole.MANAGER,), priority=30),
    rule(min_working_days=Decimal("5.01"), steps=(StepRole.MANAGER, StepRole.HR), priority=40),
]


@pytest.mark.parametrize(
    ("leave", "days", "expected_steps", "auto"),
    [
        (ANNUAL, 1, (StepRole.MANAGER,), False),
        (ANNUAL, 5, (StepRole.MANAGER,), False),
        (ANNUAL, 6, (StepRole.MANAGER, StepRole.HR), False),
        (UNPAID, 7, (StepRole.MANAGER, StepRole.HR), False),
        (SICK, 2, (), True),
        (SICK, 3, (), True),
        (SICK, 4, (StepRole.HR,), False),
    ],
)
def test_selection_table_from_doc01(
    leave: LeaveType, days: int, expected_steps: tuple[StepRole, ...], auto: bool
) -> None:
    selected = select_rule(SEED_RULES, leave.id, days)
    assert selected is not None
    assert selected.steps == expected_steps and selected.auto_acknowledge is auto


def test_inactive_rules_and_priority() -> None:
    specific = rule(leave_type_id=ANNUAL.id, steps=(StepRole.HR,), priority=5)
    inactive = rule(leave_type_id=ANNUAL.id, steps=(), priority=1, active=False)
    assert select_rule([*SEED_RULES, specific, inactive], ANNUAL.id, 2) is specific
    assert select_rule([], ANNUAL.id, 2) is None


REQUESTER, MANAGER, OVERRIDE = UUID(int=11), UUID(int=12), UUID(int=13)


def test_routing_manager_step_designated_to_department_manager() -> None:
    decision = route(
        SEED_RULES[2],
        leave_type=ANNUAL,
        requester_id=REQUESTER,
        department_manager_id=MANAGER,
        manager_override_id=None,
        has_document=False,
        within_cap=True,
    )
    assert [(s.step_no, s.approver_role, s.designated_employee_id) for s in decision.steps] == [
        (1, StepRole.MANAGER, MANAGER)
    ]
    assert not decision.auto_acknowledged


def test_routing_manager_override_wins() -> None:
    decision = route(
        SEED_RULES[2],
        leave_type=ANNUAL,
        requester_id=REQUESTER,
        department_manager_id=MANAGER,
        manager_override_id=OVERRIDE,
        has_document=False,
        within_cap=True,
    )
    assert decision.steps[0].designated_employee_id == OVERRIDE


def test_managers_own_request_goes_to_override_or_hr() -> None:
    to_override = route(
        SEED_RULES[2],
        leave_type=ANNUAL,
        requester_id=MANAGER,
        department_manager_id=MANAGER,
        manager_override_id=OVERRIDE,
        has_document=False,
        within_cap=True,
    )
    assert (
        to_override.steps[0].designated_employee_id == OVERRIDE
        and to_override.steps[0].approver_role == StepRole.MANAGER
    )
    to_hr = route(
        SEED_RULES[2],
        leave_type=ANNUAL,
        requester_id=MANAGER,
        department_manager_id=MANAGER,
        manager_override_id=None,
        has_document=False,
        within_cap=True,
    )
    assert [(s.approver_role, s.designated_employee_id) for s in to_hr.steps] == [(StepRole.HR, None)]
    two_steps = route(
        SEED_RULES[3],
        leave_type=ANNUAL,
        requester_id=MANAGER,
        department_manager_id=MANAGER,
        manager_override_id=None,
        has_document=False,
        within_cap=True,
    )
    assert [s.approver_role for s in two_steps.steps] == [
        StepRole.HR
    ]  # manager→HR collapses with the HR step


def test_no_department_manager_routes_to_hr() -> None:
    decision = route(
        SEED_RULES[2],
        leave_type=ANNUAL,
        requester_id=REQUESTER,
        department_manager_id=None,
        manager_override_id=None,
        has_document=False,
        within_cap=True,
    )
    assert [s.approver_role for s in decision.steps] == [StepRole.HR]


def test_sick_auto_acknowledgement_conditions() -> None:
    ok = route(
        SEED_RULES[0],
        leave_type=SICK,
        requester_id=REQUESTER,
        department_manager_id=MANAGER,
        manager_override_id=None,
        has_document=False,
        within_cap=True,
    )
    assert ok.auto_acknowledged and ok.steps == ()
    documented = route(
        SEED_RULES[0],
        leave_type=SICK,
        requester_id=REQUESTER,
        department_manager_id=MANAGER,
        manager_override_id=None,
        has_document=True,
        within_cap=True,
    )
    assert not documented.auto_acknowledged and [s.approver_role for s in documented.steps] == [StepRole.HR]
    over_cap = route(
        SEED_RULES[0],
        leave_type=SICK,
        requester_id=REQUESTER,
        department_manager_id=MANAGER,
        manager_override_id=None,
        has_document=False,
        within_cap=False,
    )
    assert not over_cap.auto_acknowledged and [s.approver_role for s in over_cap.steps] == [StepRole.HR]


def test_sick_leave_never_has_a_manager_step() -> None:
    """Even a misconfigured rule with a manager step for sick leave is re-routed to HR (medical confidentiality)."""
    bad_rule = rule(leave_type_id=SICK.id, steps=(StepRole.MANAGER,), priority=1)
    decision = route(
        bad_rule,
        leave_type=SICK,
        requester_id=REQUESTER,
        department_manager_id=MANAGER,
        manager_override_id=None,
        has_document=False,
        within_cap=True,
    )
    assert [s.approver_role for s in decision.steps] == [StepRole.HR]


def test_document_required_flag() -> None:
    decision = route(
        SEED_RULES[1],
        leave_type=SICK,
        requester_id=REQUESTER,
        department_manager_id=MANAGER,
        manager_override_id=None,
        has_document=False,
        within_cap=True,
    )
    assert decision.document_required and [s.approver_role for s in decision.steps] == [StepRole.HR]
