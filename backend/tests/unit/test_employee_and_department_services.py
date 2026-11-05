"""Company administration services: Keycloak + database consistency, guards, auditing, tenant boundaries."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import (
    AuthorizationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
)
from leave_assistant.application.ports import EmployeeFilter
from leave_assistant.application.tenancy.commands import (
    CreateDepartment,
    CreateEmployee,
    UpdateDepartment,
    UpdateEmployee,
)
from leave_assistant.domain.errors import InvalidValue
from leave_assistant.domain.tenancy import EmployeeStatus, Locale, Role
from tests.conftest import principal_for
from tests.fakes.identity import FakeIdentityAdmin
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World


def new_employee_cmd(world: World, **overrides: object) -> CreateEmployee:
    base: dict[str, object] = {
        "email": "New.Person@aegean.example",
        "first_name": "New",
        "last_name": "Person",
        "employee_number": "aeg-100",
        "department_id": world.a.departments["OPS"].id,
        "locale": Locale.EL,
        "hire_date": date(2026, 1, 12),
        "roles": frozenset({Role.EMPLOYEE}),
    }
    base.update(overrides)
    return CreateEmployee(**base)  # type: ignore[arg-type]


async def test_create_employee_provisions_identity_and_row(
    services: Services, world: World, store: MemoryStore, identity: FakeIdentityAdmin
) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    created = await services.employees.create(admin, new_employee_cmd(world))
    employee = created.employee
    assert employee.email == "new.person@aegean.example" and employee.employee_number == "AEG-100"
    assert created.temporary_password and len(created.temporary_password) == 16
    user = identity.users[employee.keycloak_sub]
    assert user.temporary and user.realm_roles == {"employee"} and user.group_paths == {"/tenants/aegean"}
    assert store.employees[employee.id].roles == {Role.EMPLOYEE}
    entry = store.audit[-1]
    assert entry.action == "admin.employee.created" and entry.tenant_id == world.a.tenant.id
    assert entry.actor_id == admin.user_id and entry.details["roles"] == ["employee"]


async def test_seed_style_password_is_not_temporary(
    services: Services, world: World, identity: FakeIdentityAdmin
) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    created = await services.employees.create(
        admin, new_employee_cmd(world, password="known-dev", temporary_password=False)
    )
    assert created.temporary_password is None
    assert identity.users[created.employee.keycloak_sub].password == "known-dev"
    assert not identity.users[created.employee.keycloak_sub].temporary


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"department_id": uuid4()}, InvalidInputError),
        ({"roles": frozenset()}, InvalidValue),
        ({"roles": frozenset({Role.PLATFORM_ADMIN})}, InvalidValue),
        ({"email": "not-an-email"}, InvalidValue),
        ({"manager_override_employee_id": uuid4()}, InvalidInputError),
    ],
)
async def test_create_employee_validation(
    services: Services, world: World, overrides: dict, error: type[Exception], identity: FakeIdentityAdmin
) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    with pytest.raises(error):
        await services.employees.create(admin, new_employee_cmd(world, **overrides))
    assert not any(name == "create_user" for name, _ in identity.calls)


async def test_create_employee_duplicate_email_conflicts(services: Services, world: World) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    existing = world.a.employees["employee"]
    with pytest.raises(ConflictError):
        await services.employees.create(admin, new_employee_cmd(world, email=existing.email))


async def test_only_company_admins_manage_employees(services: Services, world: World) -> None:
    for role in ("employee", "manager", "hr_admin"):
        with pytest.raises(AuthorizationError):
            await services.employees.create(principal_for(world.a.employees[role]), new_employee_cmd(world))


async def test_cross_tenant_employee_is_not_found(services: Services, world: World) -> None:
    admin_b = principal_for(world.b.employees["company_admin"])
    with pytest.raises(NotFoundError):
        await services.employees.get(admin_b, world.a.employees["employee"].id)
    page = await services.employees.list(admin_b, filters=EmployeeFilter(), limit=None, cursor=None)
    assert {e.tenant_id for e in page.items} == {world.b.tenant.id}


async def test_update_syncs_identity_and_audits(
    services: Services, world: World, store: MemoryStore, identity: FakeIdentityAdmin
) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    target = world.a.employees["employee"]
    identity.users[target.keycloak_sub] = __import__("tests.fakes.identity", fromlist=["FakeUser"]).FakeUser(
        sub=target.keycloak_sub,
        username=target.email,
        email=target.email,
        first_name="x",
        last_name="y",
        locale="el",
        password="p",
        temporary=False,
    )
    updated = await services.employees.update(
        admin,
        target.id,
        UpdateEmployee(
            first_name="Renamed",
            department_id=world.a.departments["FIN"].id,
            manager_override_employee_id=world.a.employees["manager"].id,
        ),
    )
    assert updated.first_name == "Renamed" and updated.department_id == world.a.departments["FIN"].id
    assert identity.users[target.keycloak_sub].first_name == "Renamed"
    assert store.audit[-1].action == "admin.employee.updated"
    assert set(store.audit[-1].details["changes"]) == {
        "first_name",
        "department_id",
        "manager_override_employee_id",
    }
    cleared = await services.employees.update(
        admin, target.id, UpdateEmployee(manager_override_employee_id=None)
    )
    assert cleared.manager_override_employee_id is None
    with pytest.raises(InvalidInputError):
        await services.employees.update(
            admin, target.id, UpdateEmployee(manager_override_employee_id=target.id)
        )


async def test_deactivate_and_guards(
    services: Services, world: World, identity: FakeIdentityAdmin, store: MemoryStore
) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    target = world.a.employees["employee"]
    from tests.fakes.identity import FakeUser

    identity.users[target.keycloak_sub] = FakeUser(
        target.keycloak_sub, target.email, target.email, "a", "b", "el", "p", False
    )
    gone = await services.employees.deactivate(admin, target.id)
    assert gone.status == EmployeeStatus.INACTIVE and identity.users[target.keycloak_sub].enabled is False
    assert store.audit[-1].action == "admin.employee.deactivated"
    with pytest.raises(ConflictError):
        await services.employees.deactivate(admin, admin.employee_id)  # type: ignore[arg-type]


async def test_set_roles_mirrors_identity_and_guards_lockout(
    services: Services, world: World, identity: FakeIdentityAdmin, store: MemoryStore
) -> None:
    from tests.fakes.identity import FakeUser

    admin = principal_for(world.a.employees["company_admin"])
    target = world.a.employees["employee"]
    identity.users[target.keycloak_sub] = FakeUser(
        target.keycloak_sub,
        target.email,
        target.email,
        "a",
        "b",
        "el",
        "p",
        False,
        realm_roles={"employee", "offline_access"},
    )
    updated = await services.employees.set_roles(admin, target.id, frozenset({Role.EMPLOYEE, Role.MANAGER}))
    assert updated.roles == {Role.EMPLOYEE, Role.MANAGER}
    assert identity.users[target.keycloak_sub].realm_roles == {"employee", "manager", "offline_access"}
    assert store.audit[-1].action == "admin.role.changed" and store.audit[-1].details == {
        "from": ["employee"],
        "to": ["employee", "manager"],
    }
    with pytest.raises(ConflictError):
        await services.employees.set_roles(admin, admin.employee_id, frozenset({Role.EMPLOYEE}))  # type: ignore[arg-type]
    with pytest.raises(InvalidValue):
        await services.employees.set_roles(admin, target.id, frozenset({Role.AUTOMATION}))


async def test_department_lifecycle(services: Services, world: World, store: MemoryStore) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    dept = await services.departments.create(admin, CreateDepartment(code="it", name="  IT  Support "))
    assert dept.code == "IT" and dept.name == "IT Support"
    with pytest.raises(ConflictError):
        await services.departments.create(admin, CreateDepartment(code="IT", name="dup"))
    renamed = await services.departments.update(
        admin, dept.id, UpdateDepartment(name="Information Technology")
    )
    assert renamed.name == "Information Technology"
    with pytest.raises(ConflictError, match="still has employees"):
        await services.departments.delete(admin, world.a.departments["OPS"].id)
    await services.departments.delete(admin, dept.id)
    assert dept.id not in store.departments
    assert [e.action for e in store.audit] == [
        "admin.department.created",
        "admin.department.updated",
        "admin.department.deleted",
    ]
    with pytest.raises(NotFoundError):
        await services.departments.get(
            principal_for(world.b.employees["company_admin"]), world.a.departments["OPS"].id
        )


async def test_set_manager_rules(services: Services, world: World, store: MemoryStore) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    fin = world.a.departments["FIN"]
    with pytest.raises(InvalidInputError, match="manager role"):
        await services.departments.set_manager(
            admin, fin.id, world.a.employees["employee2"].id
        )  # in FIN, no manager role
    with pytest.raises(InvalidInputError, match="member of the department"):
        await services.departments.set_manager(
            admin, fin.id, world.a.employees["manager"].id
        )  # manager of OPS
    cleared = await services.departments.set_manager(admin, world.a.departments["OPS"].id, None)
    assert cleared.manager_employee_id is None
    assert store.audit[-1].action == "admin.department.manager.changed"
