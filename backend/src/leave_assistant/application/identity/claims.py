"""Claims the backend trusts from a validated Keycloak token (doc 08 §4). Everything else is ignored."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from leave_assistant.application.errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class TokenClaims:
    sub: str
    username: str
    roles: frozenset[str]
    tenant_ids: tuple[str, ...]
    expires_at: int
    locale: str | None = None
    azp: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TokenClaims:
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise AuthenticationError("token has no subject")
        realm_access = payload.get("realm_access") or {}
        roles = realm_access.get("roles") or []
        if not isinstance(roles, list):
            raise AuthenticationError("malformed roles claim")
        raw_tenant = payload.get("tenant_id")
        tenant_ids: tuple[str, ...]
        if raw_tenant is None:
            tenant_ids = ()
        elif isinstance(raw_tenant, str):
            tenant_ids = (raw_tenant,) if raw_tenant else ()
        elif isinstance(raw_tenant, list) and all(isinstance(v, str) for v in raw_tenant):
            tenant_ids = tuple(v for v in raw_tenant if v)
        else:
            raise AuthenticationError("malformed tenant claim")
        return cls(
            sub=sub,
            username=str(payload.get("preferred_username") or sub),
            roles=frozenset(str(r) for r in roles),
            tenant_ids=tenant_ids,
            expires_at=int(payload.get("exp", 0)),
            locale=payload.get("locale") if isinstance(payload.get("locale"), str) else None,
            azp=payload.get("azp") if isinstance(payload.get("azp"), str) else None,
        )
