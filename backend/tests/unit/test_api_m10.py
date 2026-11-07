"""M10 endpoints through HTTP: platform settings (defaults for new companies), company settings, audit CSV export."""

from __future__ import annotations

import csv
import io
from uuid import uuid4

from httpx import AsyncClient

from leave_assistant.application.audit import export as audit_export
from tests.conftest import TokenFactory, bearer
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


async def test_platform_settings_are_the_defaults_of_new_companies(
    api: AsyncClient, world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    platform = bearer(tokens.platform_admin())
    admin = bearer(tokens.for_employee(world.a.employees["company_admin"]))
    current = await api.get("/api/v1/platform/settings", headers=platform)
    assert current.status_code == 200 and current.json()["default_locale"] == "en"
    assert (await api.get("/api/v1/platform/settings", headers=admin)).status_code == 403
    changed = await api.put(
        "/api/v1/platform/settings",
        json={"default_locale": "el", "timezone": "Europe/Athens", "settings": {"proposal_ttl_minutes": 30}},
        headers=platform,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["tenant_settings"]["proposal_ttl_minutes"] == 30
    bad = await api.put("/api/v1/platform/settings", json={"timezone": "Mars/Olympus"}, headers=platform)
    assert bad.status_code == 422
    unknown = await api.put(
        "/api/v1/platform/settings", json={"settings": {"colour": "red"}}, headers=platform
    )
    assert unknown.status_code == 422

    created = await api.post(
        "/api/v1/platform/tenants",
        json={"slug": "kyklades", "name": "Kyklades Ferries", "settings": {"work_week": [0, 1, 2, 3, 4, 5]}},
        headers={**platform, **key()},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["default_locale"] == "el" and body["timezone"] == "Europe/Athens"
    assert body["settings"]["proposal_ttl_minutes"] == 30  # from the platform settings
    assert body["settings"]["work_week"] == [0, 1, 2, 3, 4, 5]  # the creation's own value wins
    assert [e.action for e in store.audit if e.action == "platform.settings.changed"]


async def test_company_settings_round_trip_and_refuse_platform_fields(
    api: AsyncClient, world: World, tokens: TokenFactory, store: MemoryStore
) -> None:
    admin = bearer(tokens.for_employee(world.a.employees["company_admin"]))
    hr = bearer(tokens.for_employee(world.a.employees["hr_admin"]))
    current = await api.get("/api/v1/admin/settings", headers=admin)
    assert current.status_code == 200 and current.json()["slug"] == world.a.tenant.slug
    assert (await api.get("/api/v1/admin/settings", headers=hr)).status_code == 403
    changed = await api.put(
        "/api/v1/admin/settings",
        json={
            "timezone": "Europe/Dublin",
            "settings": {"allow_hr_fallback": False, "work_week": [0, 1, 2, 3]},
        },
        headers=admin,
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["timezone"] == "Europe/Dublin" and body["settings"]["work_week"] == [0, 1, 2, 3]
    assert body["settings"]["allow_hr_fallback"] is False
    assert store.tenants[world.a.tenant.id].settings.allow_hr_fallback is False
    upload = await api.put("/api/v1/admin/settings", json={"settings": {"max_upload_mb": 40}}, headers=admin)
    assert upload.status_code == 422 and upload.json()["errors"][0]["loc"] == [
        "body",
        "settings",
        "max_upload_mb",
    ]
    week = await api.put("/api/v1/admin/settings", json={"settings": {"work_week": [9]}}, headers=admin)
    assert week.status_code == 422
    entry = next(e for e in reversed(store.audit) if e.action == "admin.tenant.settings_changed")
    assert entry.tenant_id == world.a.tenant.id and entry.details["timezone"] == "Europe/Dublin"


async def test_audit_export_is_csv_scoped_and_audited(
    api: AsyncClient, world: World, tokens: TokenFactory, store: MemoryStore, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    admin = bearer(tokens.for_employee(world.a.employees["company_admin"]))
    employee = bearer(tokens.for_employee(world.a.employees["employee"]))
    platform = bearer(tokens.platform_admin())
    await api.put(
        "/api/v1/admin/settings", json={"timezone": "Europe/Dublin"}, headers=admin
    )  # one tenant row
    await api.put(
        "/api/v1/platform/settings", json={"default_locale": "el"}, headers=platform
    )  # one platform row

    response = await api.get("/api/v1/admin/audit/export", headers=admin)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert response.content.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["occurred_at", "action", "outcome"]
    actions = {r[1] for r in rows[1:]}
    assert "admin.tenant.settings_changed" in actions and "platform.settings.changed" not in actions
    assert response.headers["x-audit-rows"] == str(len(rows) - 1)
    exported = next(e for e in reversed(store.audit) if e.action == "audit.exported")
    assert exported.details["scope"] == "tenant" and exported.details["rows"] == len(rows) - 1
    assert (await api.get("/api/v1/admin/audit/export", headers=employee)).status_code == 403

    only = await api.get("/api/v1/admin/audit/export", params={"action": "audit.exported"}, headers=admin)
    assert {r[1] for r in list(csv.reader(io.StringIO(only.content.decode("utf-8-sig"))))[1:]} == {
        "audit.exported"
    }

    plat = await api.get("/api/v1/platform/audit/export", headers=platform)
    plat_actions = {r[1] for r in list(csv.reader(io.StringIO(plat.content.decode("utf-8-sig"))))[1:]}
    assert "platform.settings.changed" in plat_actions and "admin.tenant.settings_changed" not in plat_actions

    monkeypatch.setattr(audit_export, "MAX_ROWS", 1)
    capped = await api.get("/api/v1/admin/audit/export", headers=admin)
    lines = list(csv.reader(io.StringIO(capped.content.decode("utf-8-sig"))))
    assert (
        capped.headers["x-audit-truncated"] == "true"
        and len(lines) == 3
        and lines[-1][0].startswith("# truncated")
    )
