"""Seed data validity and idempotency (FR-TEN-08)."""

from __future__ import annotations

from leave_assistant.application.container import Services
from leave_assistant.domain.tenancy import Role, validate_slug
from leave_assistant.seed.data import COMPANIES
from leave_assistant.seed.run import run_seed
from tests.fakes.identity import FakeIdentityAdmin
from tests.fakes.memory import MemoryStore


def test_seed_data_is_consistent() -> None:
    assert [c.slug for c in COMPANIES] == ["aegean", "brightwater"]
    for company in COMPANIES:
        validate_slug(company.slug)
        codes = {d.code for d in company.departments}
        numbers = [e.number for e in company.employees]
        emails = [e.email for e in company.employees]
        assert len(numbers) == len(set(numbers)) and len(emails) == len(set(emails))
        assert all(e.email.endswith(".example") for e in company.employees)
        assert all(e.department_code in codes for e in company.employees)
        by_number = {e.number: e for e in company.employees}
        for dept in company.departments:
            assert dept.manager_number is not None
            manager = by_number[dept.manager_number]
            assert Role.MANAGER in manager.roles and manager.department_code == dept.code
        roles = {r for e in company.employees for r in e.roles}
        assert {Role.COMPANY_ADMIN, Role.HR_ADMIN, Role.MANAGER, Role.EMPLOYEE} <= roles
        assert all(
            e.roles <= {Role.EMPLOYEE, Role.MANAGER, Role.HR_ADMIN, Role.COMPANY_ADMIN}
            for e in company.employees
        )


async def test_seed_runs_idempotently(
    services: Services, store: MemoryStore, identity: FakeIdentityAdmin
) -> None:
    first = await run_seed(services, "dev-password")
    assert len([x for x in first.created if x.startswith("tenant:")]) == 2
    assert len([x for x in first.created if x.startswith("department:")]) == 7
    assert len([x for x in first.created if x.startswith("employee:")]) == 18
    assert len(store.employees) == 18 and len(identity.users) == 18
    assert all(not u.temporary and u.password == "dev-password" for u in identity.users.values())
    assert sum(1 for d in store.departments.values() if d.manager_employee_id is not None) == 7
    el_at_brightwater = [
        e for e in store.employees.values() if e.email == "dimitra.nikolaou@brightwater.example"
    ]
    assert el_at_brightwater[0].locale.value == "el"

    identity.calls.clear()
    second = await run_seed(services, "dev-password")
    assert second.changed == 0 and len(second.unchanged) == 27
    assert not any(name in {"create_user", "set_realm_roles", "update_user"} for name, _ in identity.calls)
