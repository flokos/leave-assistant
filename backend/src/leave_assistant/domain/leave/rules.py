"""Approval-rules engine (ADR-0006, ADR-0028): rule selection and step materialisation — deterministic data rules."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from leave_assistant.domain.leave.types import ApprovalRule, LeaveType, StepRole

__all__ = ["RoutingDecision", "StepRole", "StepSpec", "route", "select_rule"]


@dataclass(frozen=True, slots=True)
class StepSpec:
    step_no: int
    approver_role: StepRole
    designated_employee_id: (
        UUID | None
    )  # manager steps: the designated manager; HR steps: any HR administrator


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    rule: ApprovalRule
    steps: tuple[StepSpec, ...]
    auto_acknowledged: bool
    document_required: bool


def select_rule(
    rules: Sequence[ApprovalRule], leave_type_id: UUID, working_days: int | Decimal
) -> ApprovalRule | None:
    """First matching active rule by ascending priority (doc 07 §4.3)."""
    days = Decimal(working_days)
    for rule in sorted(rules, key=lambda r: (r.priority, r.id.int)):
        if rule.matches(leave_type_id, days):
            return rule
    return None


def route(
    rule: ApprovalRule,
    *,
    leave_type: LeaveType,
    requester_id: UUID,
    department_manager_id: UUID | None,
    manager_override_id: UUID | None,
    has_document: bool,
    within_cap: bool,
) -> RoutingDecision:
    """Materialise the approval steps for one request.

    - Auto-acknowledgement applies only without a supporting document and within the balance cap (Q-02); otherwise
      the request gets an HR review step.
    - Sick leave never has a manager step (medical confidentiality): manager steps become HR steps.
    - A manager step is designated to the manager override, else the department manager; when the designee is the
      requester (manager's own request) or there is none, the step becomes an HR step (Q-03).
    - Consecutive HR steps collapse into one.
    """
    roles: list[StepRole] = list(rule.steps)
    auto = bool(rule.auto_acknowledge and not has_document and within_cap)
    if rule.auto_acknowledge and not auto:
        roles = [StepRole.HR]  # documented or cap-exceeding sick leave → HR review instead of acknowledgement
    specs: list[tuple[StepRole, UUID | None]] = []
    for role in roles:
        if role is StepRole.MANAGER and not leave_type.is_sick:
            designee = manager_override_id or department_manager_id
            if designee is not None and designee != requester_id:
                specs.append((StepRole.MANAGER, designee))
                continue
        specs.append((StepRole.HR, None))
    collapsed: list[tuple[StepRole, UUID | None]] = []
    for spec in specs:
        if collapsed and collapsed[-1] == spec:
            continue
        collapsed.append(spec)
    steps = tuple(StepSpec(i + 1, role, designee) for i, (role, designee) in enumerate(collapsed))
    return RoutingDecision(
        rule=rule,
        steps=steps if not auto else (),
        auto_acknowledged=auto,
        document_required=rule.requires_document,
    )
