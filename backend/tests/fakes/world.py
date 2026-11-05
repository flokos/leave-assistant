"""A seeded in-memory world: two tenants with departments and one employee per role (doc 11 §6: A = Aegean, B = Brightwater)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from leave_assistant.domain.tenancy import (
    Department,
    Employee,
    EmployeeStatus,
    Locale,
    Role,
    Tenant,
    TenantSettings,
    TenantStatus,
)
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock, SequentialIds


@dataclass
class TenantWorld:
    tenant: Tenant
    departments: dict[str, Department] = field(default_factory=dict)
    employees: dict[str, Employee] = field(default_factory=dict)  # key: role name or custom label


@dataclass
class World:
    a: TenantWorld
    b: TenantWorld

    def tenant(self, key: str) -> TenantWorld:
        return self.a if key == "a" else self.b


def _tenant(ids: SequentialIds, clock: FixedClock, slug: str, name: str, locale: Locale, tz: str) -> Tenant:
    now = clock.now()
    return Tenant(
        id=ids.new_id(),
        slug=slug,
        name=name,
        status=TenantStatus.ACTIVE,
        default_locale=locale,
        timezone=tz,
        settings=TenantSettings(),
        created_at=now,
        updated_at=now,
        keycloak_group_id=f"group-{slug}",
    )


def _department(ids: SequentialIds, clock: FixedClock, tenant: Tenant, code: str, name: str) -> Department:
    now = clock.now()
    return Department(
        id=ids.new_id(), tenant_id=tenant.id, code=code, name=name, created_at=now, updated_at=now
    )


def _employee(
    ids: SequentialIds,
    clock: FixedClock,
    tenant: Tenant,
    department: Department,
    number: str,
    first: str,
    last: str,
    roles: set[Role],
    *,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
) -> Employee:
    now = clock.now()
    email = f"{first}.{last}@{tenant.slug}.example".lower()
    return Employee(
        id=ids.new_id(),
        tenant_id=tenant.id,
        keycloak_sub=f"sub-{tenant.slug}-{number.lower()}",
        employee_number=number,
        first_name=first,
        last_name=last,
        email=email,
        department_id=department.id,
        hire_date=date(2022, 1, 10),
        locale=tenant.default_locale,
        status=status,
        created_at=now,
        updated_at=now,
        roles=frozenset(roles),
    )


def build_world(store: MemoryStore, clock: FixedClock, ids: SequentialIds) -> World:
    worlds: list[TenantWorld] = []
    specs = (
        ("aegean", "Aegean Logistics S.A.", Locale.EL, "Europe/Athens"),
        ("brightwater", "Brightwater Software Ltd.", Locale.EN, "Europe/Dublin"),
    )
    for slug, name, locale, tz in specs:
        tenant = _tenant(ids, clock, slug, name, locale, tz)
        store.tenants[tenant.id] = tenant
        ops = _department(ids, clock, tenant, "OPS", "Operations")
        hr = _department(ids, clock, tenant, "HR", "Human Resources")
        fin = _department(ids, clock, tenant, "FIN", "Finance")
        for d in (ops, hr, fin):
            store.departments[d.id] = d
        tw = TenantWorld(tenant=tenant, departments={"OPS": ops, "HR": hr, "FIN": fin})
        prefix = slug[:3].upper()
        tw.employees["company_admin"] = _employee(
            ids,
            clock,
            tenant,
            hr,
            f"{prefix}-001",
            "Admin",
            slug.title(),
            {Role.COMPANY_ADMIN, Role.EMPLOYEE},
        )
        tw.employees["hr_admin"] = _employee(
            ids, clock, tenant, hr, f"{prefix}-002", "Hr", slug.title(), {Role.HR_ADMIN, Role.EMPLOYEE}
        )
        tw.employees["manager"] = _employee(
            ids, clock, tenant, ops, f"{prefix}-003", "Manager", slug.title(), {Role.MANAGER, Role.EMPLOYEE}
        )
        tw.employees["employee"] = _employee(
            ids, clock, tenant, ops, f"{prefix}-004", "Worker", slug.title(), {Role.EMPLOYEE}
        )
        tw.employees["employee2"] = _employee(
            ids, clock, tenant, fin, f"{prefix}-005", "Second", slug.title(), {Role.EMPLOYEE}
        )
        tw.employees["inactive"] = _employee(
            ids,
            clock,
            tenant,
            ops,
            f"{prefix}-006",
            "Gone",
            slug.title(),
            {Role.EMPLOYEE},
            status=EmployeeStatus.INACTIVE,
        )
        ops.manager_employee_id = tw.employees["manager"].id
        for e in tw.employees.values():
            store.employees[e.id] = e
        worlds.append(tw)
    return World(a=worlds[0], b=worlds[1])


def tenant_id_of(world: World, key: str) -> UUID:
    return world.tenant(key).tenant.id
