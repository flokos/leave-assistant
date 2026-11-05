"""RSA test keys and token factory: API tests use signed tokens, never a running Keycloak (doc 11 §2)."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from leave_assistant.infrastructure.identity.jwks import StaticKeys

ISSUER = "http://localhost:8081/realms/leave-assistant"
AUDIENCE = "backend-api"


class TestKeys:
    __test__ = False  # not a pytest collection target

    def __init__(self, kid: str = "test-key-1") -> None:
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    @property
    def jwk(self) -> dict[str, Any]:
        data: dict[str, Any] = jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        return {**data, "kid": self.kid, "alg": "RS256", "use": "sig"}

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.jwk]}

    def resolver(self) -> StaticKeys:
        return StaticKeys([jwt.PyJWK(self.jwk)])

    def sign(
        self,
        claims: dict[str, Any],
        *,
        kid: str | None = None,
        algorithm: str = "RS256",
        key: Any | None = None,
        headers: dict[str, Any] | None = None,
    ) -> str:
        return jwt.encode(
            claims,
            key if key is not None else self.private,
            algorithm=algorithm,
            headers={"kid": kid or self.kid, **(headers or {})},
        )


def claims_for(
    *,
    sub: str,
    roles: list[str],
    tenant_id: UUID | str | list[str] | None = None,
    username: str | None = None,
    issuer: str = ISSUER,
    audience: str | list[str] = AUDIENCE,
    lifetime: int = 300,
    issued_at: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = issued_at if issued_at is not None else int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "iat": now,
        "exp": now + lifetime,
        "azp": "frontend-spa",
        "preferred_username": username or sub,
        "realm_access": {"roles": roles},
    }
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id if isinstance(tenant_id, list) else [str(tenant_id)]
    claims.update(extra or {})
    return claims
