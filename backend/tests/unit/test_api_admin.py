"""Platform and company administration through HTTP: idempotency, scopes, denials audited, cross-tenant invisibility."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient

from tests.conftest import TokenFactory, bearer
from tests.fakes.identity import FakeIdentityAdmin
from tests.fakes.memory import MemoryStore
from tests.fakes.tokens import claims_for
from tests.fakes.world import World

TENANT_BODY = {"slug": "testco", "name": "Test Co", "default_locale": "en", "timezone": "Europe/Dublin"}


async def test_platform_tenant_lifecycle_with_idempotency(
    api: AsyncClient, tokens: TokenFactory, identity: FakeIdentityAdmin
) -> None:
    headers = bearer(tokens.platform_admin())
    missing = await api.post("/api/v1/platform/tenants", json=TENANT_BODY, headers=headers)
    assert missing.status_code == 422 and missing.json()["errors"][0]["loc"] == ["header", "Idempotency-Key"]

    key = str(uuid4())
    created = await api.post(
        "/api/v1/platform/tenants",
        json=TENANT_BODY,
        headers=bearer(tokens.platform_admin(), **{"Idempotency-Key": key}),
    )
    assert created.status_code == 201, created.text
    tenant = created.json()
    assert (
        tenant["slug"] == "testco"
        and tenant["status"] == "active"
        and tenant["settings"]["work_week"] == [0, 1, 2, 3, 4]
    )

    replay = await api.post(
        "/api/v1/platform/tenants",
        json=TENANT_BODY,
        headers=bearer(tokens.platform_admin(), **{"Idempotency-Key": key}),
    )
    assert (
        replay.status_code == 201
        and replay.headers["Idempotent-Replayed"] == "true"
        and replay.json() == tenant
    )

    mismatch = await api.post(
        "/api/v1/platform/tenants",
        json={**TENANT_BODY, "name": "Other"},
        headers=bearer(tokens.platform_admin(), **{"Idempotency-Key": key}),
    )
    assert mismatch.status_code == 409 and mismatch.json()["type"].endswith("idempotency-mismatch")

    duplicate = await api.post(
        "/api/v1/platform/tenants",
        json=TENANT_BODY,
        headers=bearer(tokens.platform_admin(), **{"Idempotency-Key": str(uuid4())}),
    )
    assert duplicate.status_code == 409

    listed = await api.get("/api/v1/platform/tenants", headers=headers)
    assert [t["slug"] for t in listed.json()["items"]] == ["testco"]

    boot = await api.post(
        f"/api/v1/platform/tenants/{tenant['id']}/bootstrap-admin",
        json={"email": "ada@testco.example", "first_name": "Ada", "last_name": "Lovelace", "locale": "en"},
        headers=bearer(tokens.platform_admin(), **{"Idempotency-Key": str(uuid4())}),
    )
    assert boot.status_code == 201, boot.text
    result = boot.json()
    assert result["department"]["code"] == "ADMIN" and result["department"]["name"] == "Administration"
    assert (
        result["employee"]["roles"] == ["company_admin", "employee"]
        and len(result["temporary_password"]) == 16
    )

    sub = next(u.sub for u in identity.users.values() if u.username == "ada@testco.example")
    me = await api.get(
        "/api/v1/me",
        headers=bearer(
            tokens.raw(claims_for(sub=sub, roles=["company_admin", "employee"], tenant_id=tenant["id"]))
        ),
    )
    assert (
        me.status_code == 200
        and me.json()["tenant"]["slug"] == "testco"
        and me.json()["employee"]["department"]["code"] == "ADMIN"
    )

    suspended = await api.post(
        f"/api/v1/platform/tenants/{tenant['id']}/suspend",
        headers=bearer(tokens.platform_admin(), **{"Idempotency-Key": str(uuid4())}),
    )
    assert suspended.json()["status"] == "suspended"
    assert (
        await api.get(
            "/api/v1/me",
            headers=bearer(tokens.raw(claims_for(sub=sub, roles=["company_admin"], tenant_id=tenant["id"]))),
        )
    ).status_code == 403

    audit = await api.get("/api/v1/platform/audit", headers=headers)
    actions = [e["action"] for e in audit.json()["items"]]
    assert actions[0] == "admin.tenant.config.changed" and "admin.tenant.created" in actions
    assert all(e["tenant_id"] is None for e in audit.json()["items"])  # platform rows only


async def test_platform_admin_and_automation_cannot_use_tenant_routes(
    api: AsyncClient, tokens: TokenFactory, store: MemoryStore
) -> None:
    for token in (tokens.platform_admin(), tokens.automation()):
        response = await api.get("/api/v1/admin/employees", headers=bearer(token))
        assert response.status_code == 403 and response.json()["type"].endswith("authorization-denied")
        assert (await api.get("/api/v1/admin/audit", headers=bearer(token))).status_code == 403
    assert (await api.get("/api/v1/platform/tenants", headers=bearer(tokens.automation()))).status_code == 403
    denials = [e for e in store.audit if e.action == "authz.denied"]
    assert len(denials) == 5 and all(d.tenant_id is None and d.outcome.value == "denied" for d in denials)


async def test_employee_denied_and_audited(
    api: AsyncClient, world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    employee = world.a.employees["employee"]
    response = await api.get(
        "/api/v1/admin/employees", headers=bearer(tokens.for_employee(employee), **{"X-Request-Id": "deny-1"})
    )
    assert response.status_code == 403
    (denial,) = [e for e in store.audit if e.action == "authz.denied"]
    assert denial.tenant_id == world.a.tenant.id and denial.actor_id == employee.keycloak_sub
    assert denial.request_id == "deny-1" and denial.details == {"action": "route"}
    # the tenant's HR admin sees the denial in the audit log; tenant B's admin does not
    hr = await api.get(
        "/api/v1/admin/audit",
        params={"action": "authz.denied"},
        headers=bearer(tokens.for_employee(world.a.employees["hr_admin"])),
    )
    assert hr.status_code == 200 and [e["actor_id"] for e in hr.json()["items"]] == [employee.keycloak_sub]
    other = await api.get(
        "/api/v1/admin/audit", headers=bearer(tokens.for_employee(world.b.employees["company_admin"]))
    )
    assert other.status_code == 200 and other.json()["items"] == []


async def test_company_admin_employee_management(
    api: AsyncClient, world: World, tokens: TokenFactory, identity: FakeIdentityAdmin
) -> None:
    admin = world.a.employees["company_admin"]
    auth = bearer(tokens.for_employee(admin))
    listed = await api.get("/api/v1/admin/employees", headers=auth)
    assert listed.status_code == 200
    numbers = [e["employee_number"] for e in listed.json()["items"]]
    assert len(numbers) == 6 and all(n.startswith("AEG") for n in numbers)

    page1 = await api.get("/api/v1/admin/employees", params={"limit": 4}, headers=auth)
    assert len(page1.json()["items"]) == 4 and page1.json()["next_cursor"]
    page2 = await api.get(
        "/api/v1/admin/employees", params={"limit": 4, "cursor": page1.json()["next_cursor"]}, headers=auth
    )
    assert len(page2.json()["items"]) == 2 and page2.json()["next_cursor"] is None
    filtered = await api.get("/api/v1/admin/employees", params={"status": "inactive"}, headers=auth)
    assert [e["employee_number"] for e in filtered.json()["items"]] == ["AEG-006"]

    other_tenant_employee = world.b.employees["employee"]
    assert (
        await api.get(f"/api/v1/admin/employees/{other_tenant_employee.id}", headers=auth)
    ).status_code == 404

    body = {
        "email": "nea.ergazomeni@aegean.example",
        "first_name": "Νέα",
        "last_name": "Εργαζόμενη",
        "employee_number": "AEG-200",
        "department_id": str(world.a.departments["FIN"].id),
        "locale": "el",
        "hire_date": "2026-02-02",
        "roles": ["employee"],
    }
    created = await api.post(
        "/api/v1/admin/employees",
        json=body,
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "k1"}),
    )
    assert created.status_code == 201, created.text
    new_id = created.json()["employee"]["id"]
    assert created.json()["temporary_password"]
    assert identity.users[created.json()["employee"]["id"] and next(iter(identity.users))].temporary

    bad = await api.post(
        "/api/v1/admin/employees",
        json={**body, "email": "x@y.example", "department_id": str(uuid4())},
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "k2"}),
    )
    assert bad.status_code == 422 and bad.json()["errors"][0]["loc"] == ["body", "department_id"]

    patched = await api.patch(
        f"/api/v1/admin/employees/{new_id}",
        json={"first_name": "Νέα-2"},
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "k3"}),
    )
    assert patched.status_code == 200 and patched.json()["first_name"] == "Νέα-2"

    roles = await api.put(
        f"/api/v1/admin/employees/{new_id}/roles",
        json={"roles": ["employee", "manager"]},
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "k4"}),
    )
    assert roles.status_code == 200 and roles.json()["roles"] == ["employee", "manager"]

    manager = await api.put(
        f"/api/v1/admin/departments/{world.a.departments['FIN'].id}/manager",
        json={"employee_id": new_id},
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "k5"}),
    )
    assert manager.status_code == 200 and manager.json()["manager_employee_id"] == new_id

    deactivated = await api.post(
        f"/api/v1/admin/employees/{new_id}/deactivate",
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "k6"}),
    )
    assert deactivated.status_code == 200 and deactivated.json()["status"] == "inactive"

    self_lockout = await api.post(
        f"/api/v1/admin/employees/{admin.id}/deactivate",
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "k7"}),
    )
    assert self_lockout.status_code == 409

    audit = await api.get("/api/v1/admin/audit", headers=auth)
    actions = [e["action"] for e in audit.json()["items"]]
    assert actions[:5] == [
        "admin.employee.deactivated",
        "admin.department.manager.changed",
        "admin.role.changed",
        "admin.employee.updated",
        "admin.employee.created",
    ]


async def test_company_admin_departments(api: AsyncClient, world: World, tokens: TokenFactory) -> None:
    admin = world.a.employees["company_admin"]
    auth = bearer(tokens.for_employee(admin))
    assert [d["code"] for d in (await api.get("/api/v1/admin/departments", headers=auth)).json()] == [
        "FIN",
        "HR",
        "OPS",
    ]
    created = await api.post(
        "/api/v1/admin/departments",
        json={"code": "it", "name": "IT"},
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "d1"}),
    )
    assert created.status_code == 201 and created.json()["code"] == "IT"
    dept_id = created.json()["id"]
    not_empty = await api.delete(
        f"/api/v1/admin/departments/{world.a.departments['OPS'].id}",
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "d2"}),
    )
    assert not_empty.status_code == 409 and not_empty.json()["type"].endswith("department/not-empty")
    wrong_manager = await api.put(
        f"/api/v1/admin/departments/{dept_id}/manager",
        json={"employee_id": str(world.a.employees["employee"].id)},
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "d3"}),
    )
    assert wrong_manager.status_code == 422
    deleted = await api.delete(
        f"/api/v1/admin/departments/{dept_id}",
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "d4"}),
    )
    assert deleted.status_code == 204
    assert (await api.get(f"/api/v1/admin/departments/{dept_id}", headers=auth)).status_code == 404
    hr_auth = bearer(tokens.for_employee(world.a.employees["hr_admin"]))
    assert (await api.get("/api/v1/admin/departments", headers=hr_auth)).status_code == 403


async def test_validation_problems_follow_rfc9457(
    api: AsyncClient, world: World, tokens: TokenFactory
) -> None:
    admin = world.a.employees["company_admin"]
    response = await api.post(
        "/api/v1/admin/departments",
        json={"code": ""},
        headers=bearer(tokens.for_employee(admin), **{"Idempotency-Key": "v1"}),
    )
    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("validation-error") and body["instance"] == "/api/v1/admin/departments"
    assert any(err["loc"][-1] == "name" for err in body["errors"])
