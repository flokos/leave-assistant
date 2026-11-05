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


def _count(items: list[str], prefix: str) -> int:
    return len([x for x in items if x.startswith(prefix)])


def _anna(store: MemoryStore):  # type: ignore[no-untyped-def]
    return next(e for e in store.employees.values() if e.email == "anna.kowalski@aegean-logistics.example")


async def test_seed_runs_idempotently(
    services: Services, store: MemoryStore, identity: FakeIdentityAdmin
) -> None:
    first = await run_seed(services, "dev-password")
    assert _count(first.created, "tenant:") == 2 and _count(first.created, "department:") == 7
    assert _count(first.created, "employee:") == 18 and _count(first.created, "leave-type:") == 6
    assert _count(first.created, "policy:") == 8 and _count(first.created, "rule:") == 8
    assert _count(first.created, "holiday:") == 22 and _count(first.created, "grants:") == 2
    assert _count(first.created, "request:") == 13 and not first.unchanged
    assert len(store.employees) == 18 and len(identity.users) == 18
    assert all(not u.temporary and u.password == "dev-password" for u in identity.users.values())
    assert sum(1 for d in store.departments.values() if d.manager_employee_id is not None) == 7
    el_at_brightwater = [
        e for e in store.employees.values() if e.email == "dimitra.nikolaou@brightwater.example"
    ]
    assert el_at_brightwater[0].locale.value == "el"
    statuses = sorted(r.status.value for r in store.leave_requests.values())
    assert (
        statuses.count("COMPLETED") == 4
        and statuses.count("PENDING_MANAGER") == 2
        and statuses.count("PENDING_HR") == 2
    )
    assert (
        statuses.count("REJECTED") == 2
        and statuses.count("CANCELLED") == 1
        and statuses.count("APPROVED") == 2
    )
    grants = [e for e in store.ledger if e.entry_type.value == "GRANT"]
    assert len(grants) == 36  # 18 employees × (annual, sick), nobody hired during 2026
    assert sorted(e.quantity_days for e in grants if e.employee_id == _anna(store).id) == [10, 20]

    identity.calls.clear()
    second = await run_seed(services, "dev-password")
    assert second.changed == 0 and len(second.unchanged) == 86
    assert not any(name in {"create_user", "set_realm_roles", "update_user"} for name, _ in identity.calls)
