"""Doc 08 §5.2 permission matrix, transcribed row by row, plus scope boundaries and the own-request guard."""

from __future__ import annotations

from uuid import uuid4

import pytest

from leave_assistant.application.authz.policy import (
    PERMISSIONS,
    Action,
    ResourceRef,
    Scope,
    authorize,
    require,
)
from leave_assistant.application.errors import AuthorizationError
from leave_assistant.domain.tenancy import Principal, Role
from tests.conftest import principal_for
from tests.fakes.world import World

E, M, H, C, P = "employee", "manager", "hr_admin", "company_admin", "platform_admin"
SELF, DEPT, TENANT, PLAT = Scope.SELF, Scope.DEPARTMENT, Scope.TENANT, Scope.PLATFORM

# (action, {role: scope}) — one entry per row of the matrix in doc 08 §5.2 (— means absent)
MATRIX: list[tuple[Action, dict[str, Scope]]] = [
    (Action.READ_OWN_LEAVE, {E: SELF, M: SELF, H: SELF, C: SELF}),
    (Action.SUBMIT_OWN_REQUEST, {E: SELF, M: SELF, H: SELF, C: SELF}),
    (Action.MODIFY_OWN_REQUEST, {E: SELF, M: SELF, H: SELF, C: SELF}),
    (Action.CANCEL_OWN_REQUEST, {E: SELF, M: SELF, H: SELF, C: SELF}),
    (Action.READ_TEAM_REQUESTS, {M: DEPT, H: TENANT, C: TENANT}),
    (Action.READ_TEAM_AVAILABILITY, {M: DEPT, H: TENANT, C: TENANT}),
    (Action.READ_TEAM_LEAVE_TYPE, {M: DEPT, H: TENANT}),
    (Action.READ_SICK_DETAILS, {E: SELF, H: TENANT}),
    (Action.DECIDE_MANAGER_STEP, {M: DEPT, H: TENANT}),
    (Action.DECIDE_HR_STEP, {H: TENANT}),
    (Action.READ_DOCUMENT_GENERAL, {E: TENANT, M: TENANT, H: TENANT, C: TENANT}),
    (Action.READ_DOCUMENT_HR_RESTRICTED, {H: TENANT, C: TENANT}),
    (Action.MANAGE_DOCUMENTS_GENERAL, {H: TENANT, C: TENANT}),
    (Action.MANAGE_DOCUMENTS_HR, {H: TENANT}),
    (Action.MANAGE_DOCUMENTS_ADMIN_INTERNAL, {C: TENANT}),
    (Action.REVIEW_DOCUMENTS_HR, {H: TENANT}),
    (Action.REVIEW_DOCUMENTS_GENERAL, {C: TENANT}),
    (Action.VIEW_SUPPORTING_DOCUMENT, {E: SELF, H: TENANT}),
    (Action.RETENTION_REVIEW, {H: TENANT}),
    (Action.MANAGE_ORGANISATION, {C: TENANT}),
    (Action.MANAGE_CALENDAR_COMPANY, {C: TENANT}),
    (Action.CONNECT_PERSONAL_CALENDAR, {E: SELF, M: SELF, H: SELF, C: SELF}),
    (Action.MANAGE_SECURITY_POLICY, {C: TENANT}),
    (Action.MANAGE_SECURITY_DEFAULTS, {P: PLAT}),
    (Action.MANAGE_TENANTS, {P: PLAT}),
    (Action.READ_TENANT_DATA, {}),
    (Action.CHAT, {E: TENANT, M: TENANT, H: TENANT, C: TENANT}),
    (Action.READ_AUDIT_TENANT, {H: TENANT, C: TENANT}),
    (Action.READ_AUDIT_PLATFORM, {P: PLAT}),
    (Action.READ_RUN_HISTORY, {E: SELF, M: SELF, H: TENANT, C: TENANT}),
]


def single_role_principal(world: World, role: str) -> Principal:
    if role == P:
        return Principal(user_id="p", username="p", roles=frozenset({Role.PLATFORM_ADMIN}))
    employee = world.a.employees[role]
    return Principal(
        user_id=employee.keycloak_sub,
        username=employee.email,
        roles=frozenset({Role(role)}),  # exactly one role: cells are tested in isolation
        tenant_id=employee.tenant_id,
        employee_id=employee.id,
        department_id=employee.department_id,
    )


DECISIONS = {Action.DECIDE_MANAGER_STEP, Action.DECIDE_HR_STEP}


def satisfying_resource(world: World, principal: Principal, action: Action) -> ResourceRef:
    """A resource that satisfies every scope for this principal (decisions: someone else's request)."""
    return ResourceRef(
        kind="thing",
        id=uuid4(),
        tenant_id=principal.tenant_id,
        owner_employee_id=uuid4() if action in DECISIONS else principal.employee_id,
        department_id=principal.department_id,
        department_manager_employee_id=principal.employee_id,
    )


def test_matrix_covers_every_action() -> None:
    assert {action for action, _ in MATRIX} | {Action.INTERNAL_CALL} == set(Action)
    assert set(PERMISSIONS) == set(Action)


@pytest.mark.parametrize("role", [E, M, H, C, P])
@pytest.mark.parametrize(("action", "cells"), MATRIX, ids=[a.value for a, _ in MATRIX])
def test_matrix_cell(world: World, action: Action, cells: dict[str, Scope], role: str) -> None:
    principal = single_role_principal(world, role)
    resource = satisfying_resource(world, principal, action)
    assert authorize(principal, action, resource).allowed is (role in cells)


def test_self_scope_requires_ownership(world: World) -> None:
    me = principal_for(world.a.employees["employee"])
    other = world.a.employees["employee2"]
    assert authorize(
        me, Action.READ_OWN_LEAVE, ResourceRef("request", owner_employee_id=me.employee_id)
    ).allowed
    assert not authorize(
        me, Action.READ_OWN_LEAVE, ResourceRef("request", owner_employee_id=other.id)
    ).allowed
    assert not authorize(me, Action.READ_OWN_LEAVE, None).allowed  # resource required


def test_department_scope_requires_same_department_and_managership(world: World) -> None:
    manager = principal_for(world.a.employees["manager"])  # manager of OPS
    ops, fin = world.a.departments["OPS"], world.a.departments["FIN"]
    ok = ResourceRef("request", department_id=ops.id, department_manager_employee_id=manager.employee_id)
    other_dept = ResourceRef(
        "request", department_id=fin.id, department_manager_employee_id=manager.employee_id
    )
    not_manager = ResourceRef("request", department_id=ops.id, department_manager_employee_id=uuid4())
    unknown_manager = ResourceRef("request", department_id=ops.id)
    assert authorize(manager, Action.READ_TEAM_REQUESTS, ok).allowed
    assert not authorize(manager, Action.READ_TEAM_REQUESTS, other_dept).allowed
    assert not authorize(manager, Action.READ_TEAM_REQUESTS, not_manager).allowed
    assert not authorize(manager, Action.READ_TEAM_REQUESTS, unknown_manager).allowed  # fail closed


def test_tenant_scope_rejects_other_tenant(world: World) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    assert authorize(hr, Action.READ_TEAM_REQUESTS, None).allowed
    assert authorize(
        hr, Action.READ_TEAM_REQUESTS, ResourceRef("request", tenant_id=world.a.tenant.id)
    ).allowed
    assert not authorize(
        hr, Action.READ_TEAM_REQUESTS, ResourceRef("request", tenant_id=world.b.tenant.id)
    ).allowed


def test_nobody_decides_their_own_request(world: World) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    own = ResourceRef("request", tenant_id=hr.tenant_id, owner_employee_id=hr.employee_id)
    assert not authorize(hr, Action.DECIDE_HR_STEP, own).allowed
    assert not authorize(hr, Action.DECIDE_MANAGER_STEP, own).allowed
    someone_else = ResourceRef("request", tenant_id=hr.tenant_id, owner_employee_id=uuid4())
    assert authorize(hr, Action.DECIDE_HR_STEP, someone_else).allowed


def test_platform_admin_never_reads_tenant_data(world: World) -> None:
    platform = Principal(user_id="p", username="p", roles=frozenset({Role.PLATFORM_ADMIN}))
    for action in Action:
        if action in {Action.MANAGE_TENANTS, Action.MANAGE_SECURITY_DEFAULTS, Action.READ_AUDIT_PLATFORM}:
            assert authorize(platform, action).allowed
        else:
            assert not authorize(platform, action, ResourceRef("doc", tenant_id=world.a.tenant.id)).allowed


def test_tenant_roles_cannot_use_platform_actions(world: World) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    for action in (Action.MANAGE_TENANTS, Action.MANAGE_SECURITY_DEFAULTS, Action.READ_AUDIT_PLATFORM):
        assert not authorize(admin, action).allowed


def test_automation_only_internal() -> None:
    svc = Principal(user_id="svc", username="svc", roles=frozenset({Role.AUTOMATION}))
    assert authorize(svc, Action.INTERNAL_CALL).allowed
    assert all(not authorize(svc, a).allowed for a in Action if a is not Action.INTERNAL_CALL)


def test_combined_roles_union(world: World) -> None:
    """A manager who is also an employee reads their own request through the employee cell."""
    manager = principal_for(world.a.employees["manager"])  # roles: manager + employee
    assert authorize(
        manager, Action.READ_SICK_DETAILS, ResourceRef("r", owner_employee_id=manager.employee_id)
    ).allowed


def test_require_raises_with_action_and_resource(world: World) -> None:
    employee = principal_for(world.a.employees["employee"])
    with pytest.raises(AuthorizationError) as excinfo:
        require(employee, Action.MANAGE_ORGANISATION, ResourceRef("employee", id="42"))
    assert excinfo.value.action == Action.MANAGE_ORGANISATION.value
    assert excinfo.value.resource == "employee:42"
