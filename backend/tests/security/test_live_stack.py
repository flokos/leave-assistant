"""Security suite against the running stack (doc 11 §5): real Keycloak tokens, real API through the nginx proxy.

Requires `make up-staged && make bootstrap`. Credentials come from deploy/.env (`make test-integration` exports it).
Creates a throw-away tenant `testco-<suffix>` and removes its Keycloak users/group and database rows afterwards.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg
import pytest

from tests.fakes.tokens import TestKeys, claims_for

pytestmark = pytest.mark.keycloak

API = os.environ.get("E2E_API_URL", f"http://localhost:{os.environ.get('FRONTEND_PORT', '8080')}/api/v1")
KEYCLOAK = os.environ.get("KC_PUBLIC_URL", f"http://localhost:{os.environ.get('KEYCLOAK_PORT', '8081')}")
REALM = "leave-assistant"
TOKEN_URL = f"{KEYCLOAK}/realms/{REALM}/protocol/openid-connect/token"
ADMIN_SECRET = os.environ.get("KEYCLOAK_ADMIN_CLIENT_SECRET", "backend-admin-dev")
N8N_SECRET = os.environ.get("N8N_CLIENT_SECRET", "n8n-automation-dev")
SEED_PASSWORD = os.environ.get("SEED_USER_PASSWORD", "leave-dev")
DEV_OWNER_URL = (
    f"postgresql://app_owner:{os.environ.get('LEAVE_DB_OWNER_PASSWORD', 'app-owner-dev')}"
    f"@127.0.0.1:{os.environ.get('POSTGRES_PORT', '5432')}/leave"
)

SEED_USERS = {
    "company_admin": "giorgos.alexiou@aegean-logistics.example",
    "hr_admin": "maria.oikonomou@aegean-logistics.example",
    "manager": "nikos.papadakis@aegean-logistics.example",
    "employee": "eleni.dimitriou@aegean-logistics.example",
    "brightwater_admin": "aoife.byrne@brightwater.example",
}


@pytest.fixture(scope="module")
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=20.0) as client:
        try:
            client.get(f"{API}/healthz").raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - environment guard
            pytest.fail(f"stack not reachable at {API}: {exc}")
        yield client


def password_token(http: httpx.Client, username: str, password: str) -> httpx.Response:
    return http.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "frontend-spa",
            "username": username,
            "password": password,
        },
    )


def bearer_for(http: httpx.Client, username: str, password: str = SEED_PASSWORD) -> dict[str, str]:
    response = password_token(http, username, password)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def client_credentials(http: httpx.Client, client_id: str, secret: str) -> str:
    response = http.post(
        TOKEN_URL, data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


class KeycloakAdmin:
    """Minimal Admin API helper for assertions and cleanup (uses the backend-admin service account)."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {client_credentials(http, 'backend-admin', ADMIN_SECRET)}"}
        self._base = f"{KEYCLOAK}/admin/realms/{REALM}"

    def user(self, username: str) -> dict[str, Any] | None:
        users = self._http.get(
            f"{self._base}/users", params={"username": username, "exact": "true"}, headers=self._headers
        ).json()
        return users[0] if users else None

    def delete_user(self, username: str) -> None:
        user = self.user(username)
        if user:
            self._http.delete(f"{self._base}/users/{user['id']}", headers=self._headers)

    def reset_password(self, username: str, password: str) -> None:
        user = self.user(username)
        assert user
        response = self._http.put(
            f"{self._base}/users/{user['id']}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
            headers=self._headers,
        )
        assert response.status_code == 204, response.text

    def delete_group(self, path: str) -> None:
        response = self._http.get(f"{self._base}/group-by-path/{path.strip('/')}", headers=self._headers)
        if response.status_code == 200:
            self._http.delete(f"{self._base}/groups/{response.json()['id']}", headers=self._headers)


def me(http: httpx.Client, headers: dict[str, str]) -> dict[str, Any]:
    response = http.get(f"{API}/me", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


# --- exit criterion: /me returns correct principals for all five roles -----------------------------------
def test_me_platform_admin(http: httpx.Client) -> None:
    body = me(http, bearer_for(http, "platform.admin"))
    assert body["roles"] == ["platform_admin"] and body["is_platform_admin"] and body["tenant"] is None


@pytest.mark.parametrize(
    ("role", "expected_roles", "department"),
    [
        ("company_admin", ["company_admin", "employee"], "HR"),
        ("hr_admin", ["employee", "hr_admin", "manager"], "HR"),
        ("manager", ["employee", "manager"], "OPS"),
        ("employee", ["employee"], "OPS"),
    ],
)
def test_me_tenant_roles(http: httpx.Client, role: str, expected_roles: list[str], department: str) -> None:
    body = me(http, bearer_for(http, SEED_USERS[role]))
    assert body["roles"] == expected_roles
    assert body["tenant"]["slug"] == "aegean" and body["locale"] == "el"
    assert body["employee"]["department"]["code"] == department
    assert not body["is_platform_admin"]


def test_token_carries_single_tenant_claim_and_audience(http: httpx.Client) -> None:
    token = password_token(http, SEED_USERS["employee"], SEED_PASSWORD).json()["access_token"]
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["iss"] == f"{KEYCLOAK}/realms/{REALM}"
    assert "backend-api" in (payload["aud"] if isinstance(payload["aud"], list) else [payload["aud"]])
    assert isinstance(payload["tenant_id"], list) and len(payload["tenant_id"]) == 1
    UUID(payload["tenant_id"][0])


# --- security families (doc 11 §5) ---------------------------------------------------------------------
def test_forged_token_rejected(http: httpx.Client) -> None:
    forged = TestKeys(kid="forged").sign(
        claims_for(sub="x", roles=["platform_admin"], issuer=f"{KEYCLOAK}/realms/{REALM}")
    )
    response = http.get(f"{API}/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401 and response.json()["type"].endswith("authentication-required")


def test_service_account_cannot_call_user_endpoints(http: httpx.Client) -> None:
    headers = {"Authorization": f"Bearer {client_credentials(http, 'n8n-automation', N8N_SECRET)}"}
    assert me(http, headers)["is_automation"]
    for path in ("/admin/employees", "/admin/audit", "/platform/tenants"):
        assert http.get(f"{API}{path}", headers=headers).status_code == 403, path


def test_platform_admin_cannot_read_tenant_data(http: httpx.Client) -> None:
    headers = bearer_for(http, "platform.admin")
    for path in ("/admin/employees", "/admin/departments", "/admin/audit"):
        assert http.get(f"{API}{path}", headers=headers).status_code == 403, path


def test_cross_tenant_resources_are_invisible(http: httpx.Client) -> None:
    aegean = bearer_for(http, SEED_USERS["company_admin"])
    brightwater = bearer_for(http, SEED_USERS["brightwater_admin"])
    bw_employees = http.get(f"{API}/admin/employees", headers=brightwater).json()["items"]
    assert bw_employees and all(e["employee_number"].startswith("BWS") for e in bw_employees)
    assert http.get(f"{API}/admin/employees/{bw_employees[0]['id']}", headers=aegean).status_code == 404
    ae_numbers = {
        e["employee_number"] for e in http.get(f"{API}/admin/employees", headers=aegean).json()["items"]
    }
    assert ae_numbers and all(n.startswith("AEG") for n in ae_numbers)


def test_employee_denied_on_admin_routes_and_audited(http: httpx.Client) -> None:
    request_id = f"e2e-{uuid4().hex[:8]}"
    response = http.get(
        f"{API}/admin/employees",
        headers={**bearer_for(http, SEED_USERS["employee"]), "X-Request-Id": request_id},
    )
    assert response.status_code == 403 and response.json()["request_id"] == request_id
    hr = bearer_for(http, SEED_USERS["hr_admin"])
    denials = http.get(
        f"{API}/admin/audit", params={"action": "authz.denied", "limit": 50}, headers=hr
    ).json()["items"]
    assert any(d["request_id"] == request_id for d in denials)


# --- end-to-end provisioning flow (Q-12) -----------------------------------------------------------------
@pytest.fixture(scope="module")
def testco(http: httpx.Client) -> Iterator[dict[str, Any]]:
    suffix = secrets.token_hex(3)
    state: dict[str, Any] = {
        "slug": f"testco-{suffix}",
        "admin_email": f"ada.{suffix}@testco.example",
        "users": [],
    }
    yield state
    admin = KeycloakAdmin(http)
    for username in [state["admin_email"], *state["users"]]:
        admin.delete_user(username)
    admin.delete_group(f"/tenants/{state['slug']}")
    if state.get("tenant_id"):
        with psycopg.connect(DEV_OWNER_URL) as conn:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(state["tenant_id"]),))
            conn.execute("DELETE FROM app.employee_roles WHERE tenant_id = %s", (state["tenant_id"],))
            conn.execute("DELETE FROM app.employees WHERE tenant_id = %s", (state["tenant_id"],))
            conn.execute("DELETE FROM app.departments WHERE tenant_id = %s", (state["tenant_id"],))
            conn.execute("DELETE FROM platform.tenants WHERE id = %s", (state["tenant_id"],))
            conn.commit()


def test_bootstrap_flow_end_to_end(http: httpx.Client, testco: dict[str, Any]) -> None:
    platform = bearer_for(http, "platform.admin")
    created = http.post(
        f"{API}/platform/tenants",
        json={"slug": testco["slug"], "name": "Test Co", "default_locale": "en", "timezone": "Europe/Dublin"},
        headers={**platform, "Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 201, created.text
    testco["tenant_id"] = created.json()["id"]

    boot = http.post(
        f"{API}/platform/tenants/{testco['tenant_id']}/bootstrap-admin",
        json={"email": testco["admin_email"], "first_name": "Ada", "last_name": "Lovelace", "locale": "en"},
        headers={**platform, "Idempotency-Key": str(uuid4())},
    )
    assert boot.status_code == 201, boot.text
    temporary_password = boot.json()["temporary_password"]

    admin_api = KeycloakAdmin(http)
    user = admin_api.user(testco["admin_email"])
    assert user is not None and "UPDATE_PASSWORD" in user.get("requiredActions", [])

    # the temporary password cannot be used until it is changed (forced password change at first login)
    blocked = password_token(http, testco["admin_email"], temporary_password)
    assert blocked.status_code == 400 and blocked.json()["error"] == "invalid_grant", blocked.text
    assert "not fully set up" in blocked.json().get("error_description", "")

    admin_api.reset_password(testco["admin_email"], "Changed-Pass-1")
    company_admin = bearer_for(http, testco["admin_email"], "Changed-Pass-1")
    body = me(http, company_admin)
    assert body["tenant"]["slug"] == testco["slug"] and body["roles"] == ["company_admin", "employee"]
    assert body["employee"]["department"]["code"] == "ADMIN"

    dept = http.post(
        f"{API}/admin/departments",
        json={"code": "ENG", "name": "Engineering"},
        headers={**company_admin, "Idempotency-Key": str(uuid4())},
    )
    assert dept.status_code == 201, dept.text
    employee_email = f"grace.{testco['slug']}@testco.example"
    testco["users"].append(employee_email)
    emp = http.post(
        f"{API}/admin/employees",
        json={
            "email": employee_email,
            "first_name": "Grace",
            "last_name": "Hopper",
            "employee_number": "TC-002",
            "department_id": dept.json()["id"],
            "locale": "en",
            "hire_date": "2026-01-05",
            "roles": ["employee"],
        },
        headers={**company_admin, "Idempotency-Key": str(uuid4())},
    )
    assert emp.status_code == 201, emp.text
    employee_id = emp.json()["employee"]["id"]
    roles = http.put(
        f"{API}/admin/employees/{employee_id}/roles",
        json={"roles": ["employee", "manager"]},
        headers={**company_admin, "Idempotency-Key": str(uuid4())},
    )
    assert roles.status_code == 200 and roles.json()["roles"] == ["employee", "manager"]
    kc_user = admin_api.user(employee_email)
    assert kc_user is not None and "UPDATE_PASSWORD" in kc_user.get("requiredActions", [])

    deactivated = http.post(
        f"{API}/admin/employees/{employee_id}/deactivate",
        headers={**company_admin, "Idempotency-Key": str(uuid4())},
    )
    assert deactivated.status_code == 200 and deactivated.json()["status"] == "inactive"
    assert admin_api.user(employee_email)["enabled"] is False  # type: ignore[index]

    audit = http.get(f"{API}/admin/audit", headers=company_admin).json()["items"]
    actions = [e["action"] for e in audit]
    for expected in (
        "admin.employee.deactivated",
        "admin.role.changed",
        "admin.employee.created",
        "admin.department.created",
    ):
        assert expected in actions, actions
    platform_audit = http.get(
        f"{API}/platform/audit", params={"action": "admin.tenant.created"}, headers=platform
    ).json()["items"]
    assert any(e["details"].get("slug") == testco["slug"] for e in platform_audit)
    assert all(e["tenant_id"] is None for e in platform_audit)
