"""Every M2 endpoint through HTTP with signed tokens: shapes, problem types, idempotency (S26), versions, scopes."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient

from tests.conftest import TokenFactory, bearer
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World

ANNUAL = {"leave_type": "annual", "start_date": "2026-12-14", "end_date": "2026-12-16", "reason": "trip"}


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


async def test_catalogue_balances_ledger_and_working_days(
    api: AsyncClient, leave_world: World, tokens: TokenFactory
) -> None:
    auth = bearer(tokens.for_employee(leave_world.a.employees["employee"]))
    types = await api.get("/api/v1/leave/types", headers=auth)
    assert types.status_code == 200 and [t["code"] for t in types.json()] == ["annual", "sick", "unpaid"]
    balances = await api.get("/api/v1/leave/balances", params={"year": 2026}, headers=auth)
    assert balances.status_code == 200
    assert [(b["leave_type"], b["balance_days"], b["available_days"]) for b in balances.json()] == [
        ("annual", 20.0, 20.0),
        ("sick", 10.0, 10.0),
    ]
    ledger = await api.get("/api/v1/leave/ledger", params={"type": "annual", "year": 2026}, headers=auth)
    assert ledger.status_code == 200 and [e["entry_type"] for e in ledger.json()] == ["GRANT"]
    working = await api.get(
        "/api/v1/leave/working-days", params={"start": "2026-12-21", "end": "2026-12-27"}, headers=auth
    )
    assert working.status_code == 200 and working.json()["working_days"] == 4
    assert [e["date"] for e in working.json()["excluded_dates"]] == ["2026-12-25", "2026-12-26", "2026-12-27"]
    assert (await api.get("/api/v1/leave/types", headers=bearer(tokens.platform_admin()))).status_code == 403


async def test_validate_submit_replay_and_problems(
    api: AsyncClient, leave_world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    employee = leave_world.a.employees["employee"]
    auth = bearer(tokens.for_employee(employee))
    report = await api.post(
        "/api/v1/leave/requests/validate", json=ANNUAL, headers=auth
    )  # no Idempotency-Key needed
    assert report.status_code == 200, report.text
    assert (
        report.json()["valid"]
        and report.json()["approval_chain"] == ["manager"]
        and report.json()["balance"]["after_days"] == 17.0
    )

    missing = await api.post("/api/v1/leave/requests", json=ANNUAL, headers=auth)
    assert missing.status_code == 422 and missing.json()["errors"][0]["loc"] == ["header", "Idempotency-Key"]
    k = key()
    created = await api.post("/api/v1/leave/requests", json=ANNUAL, headers={**auth, **k})
    assert created.status_code == 201, created.text
    body = created.json()
    assert (
        body["status"] == "PENDING_MANAGER"
        and body["request_number"] == "LR-2026-000001"
        and body["working_days"] == 3.0
    )
    assert body["reason"] == "trip" and body["leave_type"] == "annual" and body["version"] == 1
    assert [s["approver_role"] for s in body["steps"]] == ["manager"] and [
        e["event_type"] for e in body["events"]
    ] == ["submitted", "routed"]

    replay = await api.post(
        "/api/v1/leave/requests", json=ANNUAL, headers={**auth, **k}
    )  # S26: duplicate submission
    assert (
        replay.status_code == 201
        and replay.headers["Idempotent-Replayed"] == "true"
        and replay.json()["id"] == body["id"]
    )
    assert len(store.leave_requests) == 1
    mismatch = await api.post(
        "/api/v1/leave/requests", json={**ANNUAL, "reason": "other"}, headers={**auth, **k}
    )
    assert mismatch.status_code == 409 and mismatch.json()["type"].endswith("idempotency-mismatch")

    overlap = await api.post(
        "/api/v1/leave/requests", json={**ANNUAL, "leave_type": "unpaid"}, headers={**auth, **key()}
    )
    assert (
        overlap.status_code == 422 and overlap.json()["type"] == "urn:leave-assistant:problem:leave/overlap"
    )
    assert overlap.json()["errors"][0]["params"]["request_number"] == "LR-2026-000001"
    retro = await api.post(
        "/api/v1/leave/requests",
        json={**ANNUAL, "start_date": "2026-11-02", "end_date": "2026-11-03"},
        headers={**auth, **key()},
    )
    assert retro.status_code == 422 and retro.json()["type"].endswith("leave/retroactive-not-allowed")
    unknown = await api.post(
        "/api/v1/leave/requests",
        json={**ANNUAL, "leave_type": "sabbatical", "start_date": "2026-12-21", "end_date": "2026-12-22"},
        headers={**auth, **key()},
    )
    assert unknown.status_code == 422 and unknown.json()["errors"][0]["loc"] == ["body", "leave_type"]

    listed = await api.get("/api/v1/leave/requests", headers=auth)
    assert [r["request_number"] for r in listed.json()["items"]] == ["LR-2026-000001"]
    filtered = await api.get("/api/v1/leave/requests", params={"status": ["APPROVED"]}, headers=auth)
    assert filtered.json()["items"] == []
    detail = await api.get(f"/api/v1/leave/requests/{body['id']}", headers=auth)
    assert detail.status_code == 200 and detail.json()["reason"] == "trip"
    other = bearer(tokens.for_employee(leave_world.a.employees["employee2"]))
    assert (await api.get(f"/api/v1/leave/requests/{body['id']}", headers=other)).status_code == 403
    foreign = bearer(tokens.for_employee(leave_world.b.employees["hr_admin"]))
    assert (await api.get(f"/api/v1/leave/requests/{body['id']}", headers=foreign)).status_code == 404

    stale = await api.post(
        f"/api/v1/leave/requests/{body['id']}/modify",
        json={"start_date": "2026-12-14", "end_date": "2026-12-15", "version": 9},
        headers={**auth, **key()},
    )
    assert stale.status_code == 409 and stale.json()["type"].endswith("version-conflict")
    modified = await api.post(
        f"/api/v1/leave/requests/{body['id']}/modify",
        json={"start_date": "2026-12-14", "end_date": "2026-12-15", "version": 1},
        headers={**auth, **key()},
    )
    assert (
        modified.status_code == 200
        and modified.json()["working_days"] == 2.0
        and modified.json()["version"] == 2
    )
    cancelled = await api.post(
        f"/api/v1/leave/requests/{body['id']}/cancel",
        json={"version": 2, "comment": "no longer needed"},
        headers={**auth, **key()},
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    again = await api.post(
        f"/api/v1/leave/requests/{body['id']}/cancel", json={"version": 3}, headers={**auth, **key()}
    )
    assert again.status_code == 409 and again.json()["type"].endswith("leave/invalid-state")


async def test_approval_flow_and_team_views(
    api: AsyncClient, leave_world: World, tokens: TokenFactory
) -> None:
    employee, manager, hr, admin = (
        leave_world.a.employees[k] for k in ("employee", "manager", "hr_admin", "company_admin")
    )
    e_auth, m_auth, h_auth, c_auth = (bearer(tokens.for_employee(x)) for x in (employee, manager, hr, admin))
    created = await api.post(
        "/api/v1/leave/requests",
        json={
            "leave_type": "annual",
            "start_date": "2026-11-16",
            "end_date": "2026-11-24",
            "reason": "holiday",
        },
        headers={**e_auth, **key()},
    )
    assert created.status_code == 201 and [s["approver_role"] for s in created.json()["steps"]] == [
        "manager",
        "hr",
    ]
    step1, step2 = created.json()["steps"]

    pending = await api.get("/api/v1/approvals/pending", headers=m_auth)
    assert pending.status_code == 200 and [i["step"]["id"] for i in pending.json()] == [step1["id"]]
    assert (
        pending.json()[0]["requester"]["employee_number"] == "AEG-004"
        and pending.json()[0]["reason"] == "holiday"
    )
    assert (await api.get("/api/v1/approvals/pending", headers=e_auth)).status_code == 403
    got = await api.get(f"/api/v1/approvals/{step1['id']}", headers=m_auth)
    assert got.status_code == 200 and got.json()["fallback"] is False
    assert (await api.get(f"/api/v1/approvals/{step1['id']}", headers=e_auth)).status_code == 403

    denied = await api.post(
        f"/api/v1/approvals/{step1['id']}/approve", json={"version": 1}, headers={**c_auth, **key()}
    )
    assert denied.status_code == 403 and denied.json()["type"].endswith("approval/not-permitted")
    early = await api.post(
        f"/api/v1/approvals/{step2['id']}/approve", json={"version": 1}, headers={**h_auth, **key()}
    )
    assert early.status_code == 409 and early.json()["type"].endswith("approval/step-not-pending")
    no_comment = await api.post(
        f"/api/v1/approvals/{step1['id']}/reject", json={"version": 1}, headers={**m_auth, **key()}
    )
    assert no_comment.status_code == 422
    approved1 = await api.post(
        f"/api/v1/approvals/{step1['id']}/approve",
        json={"version": 1, "comment": "fine"},
        headers={**m_auth, **key()},
    )
    assert approved1.status_code == 200 and approved1.json()["status"] == "PENDING_HR"
    approved2 = await api.post(
        f"/api/v1/approvals/{step2['id']}/approve", json={"version": 1}, headers={**h_auth, **key()}
    )
    assert approved2.status_code == 200 and approved2.json()["status"] == "APPROVED"
    balances = await api.get("/api/v1/leave/balances", params={"year": 2026}, headers=e_auth)
    assert [b for b in balances.json() if b["leave_type"] == "annual"][0]["available_days"] == 13.0

    team = await api.get("/api/v1/team/employees", headers=m_auth)
    assert team.status_code == 200 and [e["employee_number"] for e in team.json()] == ["AEG-003", "AEG-004"]
    assert (await api.get("/api/v1/team/employees", headers=e_auth)).status_code == 403
    availability = await api.get(
        "/api/v1/team/availability", params={"from": "2026-11-01", "to": "2026-11-30"}, headers=c_auth
    )
    assert availability.status_code == 200 and [
        (a["display_name"], a["leave_type"]) for a in availability.json()
    ] == [("Worker Aegean", None)]
    availability_m = await api.get(
        "/api/v1/team/availability", params={"from": "2026-11-01", "to": "2026-11-30"}, headers=m_auth
    )
    assert availability_m.json()[0]["leave_type"] == "annual"
    requests_h = await api.get("/api/v1/team/requests", params={"status": ["APPROVED"]}, headers=h_auth)
    assert requests_h.status_code == 200 and [r["request"]["status"] for r in requests_h.json()["items"]] == [
        "APPROVED"
    ]
    assert (await api.get("/api/v1/team/requests", headers=c_auth)).status_code == 403

    notifications = await api.get("/api/v1/notifications", params={"unread": "true"}, headers=e_auth)
    assert notifications.status_code == 200 and {n["type"] for n in notifications.json()["items"]} == {
        "request.submitted",
        "request.approved",
    }
    first = notifications.json()["items"][0]["id"]
    read = await api.post(f"/api/v1/notifications/{first}/read", headers={**e_auth, **key()})
    assert read.status_code == 200 and read.json()["read_at"] is not None
    read_all = await api.post("/api/v1/notifications/read-all", headers={**e_auth, **key()})
    assert read_all.status_code == 200 and read_all.json()["marked"] == 1
    assert (await api.get("/api/v1/notifications", params={"unread": "true"}, headers=e_auth)).json()[
        "items"
    ] == []


async def test_admin_catalogue_ledger_and_simulation(
    api: AsyncClient, leave_world: World, tokens: TokenFactory
) -> None:
    employee, hr, admin = (leave_world.a.employees[k] for k in ("employee", "hr_admin", "company_admin"))
    e_auth, h_auth, c_auth = (bearer(tokens.for_employee(x)) for x in (employee, hr, admin))
    assert (await api.get("/api/v1/admin/leave-types", headers=e_auth)).status_code == 403
    created = await api.post(
        "/api/v1/admin/leave-types",
        json={"code": "study", "name_el": "Εκπαιδευτική", "name_en": "Study", "balance_bearing": False},
        headers={**c_auth, **key()},
    )
    assert created.status_code == 201 and created.json()["code"] == "study"
    patched = await api.patch(
        f"/api/v1/admin/leave-types/{created.json()['id']}",
        json={"active": False},
        headers={**c_auth, **key()},
    )
    assert patched.status_code == 200 and patched.json()["active"] is False
    bad = await api.post(
        "/api/v1/admin/leave-types",
        json={"code": "bad code", "name_el": "x", "name_en": "x", "balance_bearing": True},
        headers={**c_auth, **key()},
    )
    assert bad.status_code == 422 and bad.json()["errors"][0]["loc"] == ["body", "code"]

    policies = await api.get("/api/v1/admin/leave-policies", params={"year": 2026}, headers=c_auth)
    assert policies.status_code == 200 and len(policies.json()) == 2
    policy = await api.post(
        "/api/v1/admin/leave-policies",
        json={"leave_type": "unpaid", "year": 2026, "entitlement_days": 0},
        headers={**c_auth, **key()},
    )
    assert policy.status_code == 201
    updated = await api.patch(
        f"/api/v1/admin/leave-policies/{policy.json()['id']}",
        json={"min_notice_days": 3, "max_consecutive_days": None},
        headers={**c_auth, **key()},
    )
    assert updated.status_code == 200 and updated.json()["min_notice_days"] == 3
    assert (
        await api.delete(f"/api/v1/admin/leave-policies/{policy.json()['id']}", headers={**c_auth, **key()})
    ).status_code == 204

    holidays = await api.get("/api/v1/admin/holidays", params={"year": 2026}, headers=c_auth)
    assert holidays.status_code == 200 and len(holidays.json()) == 12
    holiday = await api.post(
        "/api/v1/admin/holidays",
        json={"holiday_date": "2026-11-20", "name_el": "Τοπική", "name_en": "Local"},
        headers={**c_auth, **key()},
    )
    assert holiday.status_code == 201
    imported = await api.post(
        "/api/v1/admin/holidays/import",
        json={"csv": "date,name_el,name_en\n2026-11-27,Αργία,Bridge\n"},
        headers={**c_auth, **key()},
    )
    assert imported.status_code == 201 and [h["holiday_date"] for h in imported.json()] == ["2026-11-27"]
    assert (
        await api.patch(
            f"/api/v1/admin/holidays/{holiday.json()['id']}",
            json={"name_en": "Local day"},
            headers={**c_auth, **key()},
        )
    ).json()["name_en"] == "Local day"
    assert (
        await api.delete(f"/api/v1/admin/holidays/{holiday.json()['id']}", headers={**c_auth, **key()})
    ).status_code == 204

    rules = await api.get("/api/v1/admin/approval-rules", headers=c_auth)
    assert rules.status_code == 200 and [r["priority"] for r in rules.json()] == [10, 20, 30, 40]
    rule = await api.post(
        "/api/v1/admin/approval-rules",
        json={"steps": ["hr"], "leave_type": "unpaid", "priority": 5},
        headers={**c_auth, **key()},
    )
    assert rule.status_code == 201 and rule.json()["steps"] == ["hr"]
    assert (
        await api.patch(
            f"/api/v1/admin/approval-rules/{rule.json()['id']}",
            json={"active": False},
            headers={**c_auth, **key()},
        )
    ).json()["active"] is False
    assert (
        await api.delete(f"/api/v1/admin/approval-rules/{rule.json()['id']}", headers={**c_auth, **key()})
    ).status_code == 204
    simulate = await api.post(
        "/api/v1/admin/approval-rules/simulate",
        json={
            "leave_type": "annual",
            "start_date": "2026-11-16",
            "end_date": "2026-11-24",
            "employee_id": str(employee.id),
        },
        headers=c_auth,
    )
    assert simulate.status_code == 200 and simulate.json()["approval_chain"] == ["manager", "hr"]

    adjustment = await api.post(
        "/api/v1/admin/ledger/adjustments",
        json={
            "employee_id": str(employee.id),
            "leave_type": "annual",
            "quantity_days": "2.5",
            "reason": "bonus",
        },
        headers={**h_auth, **key()},
    )
    assert (
        adjustment.status_code == 201
        and adjustment.json()["entry_type"] == "ADJUSTMENT"
        and adjustment.json()["quantity_days"] == 2.5
    )
    assert (
        await api.post(
            "/api/v1/admin/ledger/adjustments",
            json={
                "employee_id": str(employee.id),
                "leave_type": "annual",
                "quantity_days": "1",
                "reason": "x",
            },
            headers={**c_auth, **key()},
        )
    ).status_code == 403
    balances = await api.get("/api/v1/admin/balances", params={"year": 2026}, headers=c_auth)
    assert balances.status_code == 200 and any(
        b["employee_id"] == str(employee.id) and b["balance_days"] == 22.5 for b in balances.json()
    )
    assert (await api.get("/api/v1/admin/balances", headers=e_auth)).status_code == 403
