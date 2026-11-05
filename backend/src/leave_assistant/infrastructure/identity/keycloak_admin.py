"""Keycloak Admin REST API client implementing the `IdentityAdmin` port (ADR-0004, FR-AUTH-07).

Authenticates with the `backend-admin` service account (client credentials). Only the operations provisioning needs
are implemented; every failure surfaces as `DependencyError` so the caller can compensate.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import httpx

from leave_assistant.application.errors import DependencyError
from leave_assistant.application.ports import IdentityUser, NewIdentityUser
from leave_assistant.config import Settings

TENANTS_GROUP = "tenants"


class KeycloakAdminClient:
    def __init__(
        self, settings: Settings, *, http: httpx.AsyncClient | None = None, timeout: float = 10.0
    ) -> None:
        self._base = settings.keycloak_url.rstrip("/")
        self._realm = settings.keycloak_realm
        self._client_id = settings.keycloak_admin_client_id
        self._client_secret = settings.keycloak_admin_client_secret.get_secret_value()
        self._http = http or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http is None
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._role_ids: dict[str, str] = {}

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # -- auth -----------------------------------------------------------------------------------------------
    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 30:
            return self._token
        try:
            response = await self._http.post(
                f"{self._base}/realms/{self._realm}/protocol/openid-connect/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DependencyError("identity provider authentication failed") from exc
        self._token = str(payload["access_token"])
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 60))
        return self._token

    async def _request(
        self, method: str, path: str, *, ok: tuple[int, ...] = (200, 201, 204), **kwargs: Any
    ) -> httpx.Response:
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        try:
            response = await self._http.request(
                method, f"{self._base}/admin/realms/{self._realm}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise DependencyError(f"identity provider unreachable ({type(exc).__name__})") from exc
        if response.status_code == 401:  # token revoked/expired early: refresh once
            self._token = None
            token = await self._access_token()
            headers["Authorization"] = f"Bearer {token}"
            response = await self._http.request(
                method, f"{self._base}/admin/realms/{self._realm}{path}", headers=headers, **kwargs
            )
        if response.status_code not in ok:
            raise DependencyError(
                f"identity provider returned HTTP {response.status_code} for {method} {path}",
                status=response.status_code,
            )
        return response

    @staticmethod
    def _id_from_location(response: httpx.Response) -> str:
        location = str(response.headers.get("Location", ""))
        if not location:
            raise DependencyError("identity provider did not return the created resource id")
        return location.rstrip("/").rsplit("/", 1)[-1]

    # -- groups ---------------------------------------------------------------------------------------------
    async def _group_by_path(self, path: str) -> dict[str, Any] | None:
        response = await self._request("GET", f"/group-by-path/{path.strip('/')}", ok=(200, 404))
        return None if response.status_code == 404 else dict(response.json())

    async def ensure_tenant_group(self, slug: str, tenant_id: UUID) -> str:
        existing = await self._group_by_path(f"{TENANTS_GROUP}/{slug}")
        if existing is not None:
            attrs = existing.get("attributes") or {}
            if attrs.get("tenant_id") != [str(tenant_id)]:
                raise DependencyError(f"group /tenants/{slug} exists with a different tenant_id attribute")
            return str(existing["id"])
        parent = await self._group_by_path(TENANTS_GROUP)
        if parent is None:
            raise DependencyError("realm is missing the /tenants group (realm export not imported?)")
        response = await self._request(
            "POST",
            f"/groups/{parent['id']}/children",
            json={"name": slug, "attributes": {"tenant_id": [str(tenant_id)]}},
        )
        return self._id_from_location(response)

    # -- users ----------------------------------------------------------------------------------------------
    async def _realm_role(self, name: str) -> dict[str, str]:
        if name not in self._role_ids:
            response = await self._request("GET", f"/roles/{name}")
            self._role_ids[name] = str(response.json()["id"])
        return {"id": self._role_ids[name], "name": name}

    async def _user_to_identity(self, data: dict[str, Any]) -> IdentityUser:
        sub = str(data["id"])
        roles = await self._request("GET", f"/users/{sub}/role-mappings/realm")
        groups = await self._request("GET", f"/users/{sub}/groups")
        return IdentityUser(
            sub=sub,
            username=str(data.get("username", "")),
            enabled=bool(data.get("enabled", False)),
            required_actions=tuple(data.get("requiredActions") or ()),
            realm_roles=frozenset(str(r["name"]) for r in roles.json()),
            group_paths=tuple(str(g.get("path", "")) for g in groups.json()),
        )

    async def find_user_by_username(self, username: str) -> IdentityUser | None:
        response = await self._request("GET", "/users", params={"username": username, "exact": "true"})
        users = response.json()
        if not users:
            return None
        return await self._user_to_identity(users[0])

    async def get_user(self, sub: str) -> IdentityUser | None:
        response = await self._request("GET", f"/users/{sub}", ok=(200, 404))
        if response.status_code == 404:
            return None
        return await self._user_to_identity(response.json())

    async def create_user(self, spec: NewIdentityUser) -> str:
        body: dict[str, Any] = {
            "username": spec.username,
            "email": spec.email,
            "emailVerified": True,
            "enabled": True,
            "firstName": spec.first_name,
            "lastName": spec.last_name,
            "attributes": {"locale": [spec.locale], **{k: list(v) for k, v in spec.attributes.items()}},
            "credentials": [
                {"type": "password", "value": spec.password, "temporary": spec.temporary_password}
            ],
            "requiredActions": ["UPDATE_PASSWORD"] if spec.temporary_password else [],
            "groups": [spec.group_path],
        }
        response = await self._request("POST", "/users", json=body)
        sub = self._id_from_location(response)
        try:
            roles = [await self._realm_role(name) for name in sorted(spec.realm_roles)]
            if roles:
                await self._request("POST", f"/users/{sub}/role-mappings/realm", json=roles)
        except DependencyError:
            await self._request("DELETE", f"/users/{sub}", ok=(204, 404))
            raise
        return sub

    async def update_user(self, sub: str, *, first_name: str, last_name: str, locale: str) -> None:
        await self._request(
            "PUT",
            f"/users/{sub}",
            json={"firstName": first_name, "lastName": last_name, "attributes": {"locale": [locale]}},
        )

    async def set_enabled(self, sub: str, enabled: bool) -> None:
        await self._request("PUT", f"/users/{sub}", json={"enabled": enabled})

    async def set_realm_roles(self, sub: str, roles: frozenset[str], *, managed: frozenset[str]) -> None:
        current = {
            str(r["name"]) for r in (await self._request("GET", f"/users/{sub}/role-mappings/realm")).json()
        }
        to_add = [await self._realm_role(name) for name in sorted(roles - current)]
        to_remove = [await self._realm_role(name) for name in sorted((current & managed) - roles)]
        if to_add:
            await self._request("POST", f"/users/{sub}/role-mappings/realm", json=to_add)
        if to_remove:
            await self._request("DELETE", f"/users/{sub}/role-mappings/realm", json=to_remove)

    async def reset_password(self, sub: str, password: str, *, temporary: bool) -> None:
        await self._request(
            "PUT",
            f"/users/{sub}/reset-password",
            json={"type": "password", "value": password, "temporary": temporary},
        )

    async def delete_user(self, sub: str) -> None:
        await self._request("DELETE", f"/users/{sub}", ok=(204, 404))
