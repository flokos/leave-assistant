"""Bearer token verification (FR-AUTH-02): signature via JWKS, issuer, audience, expiry, algorithm allowlist."""

from __future__ import annotations

from typing import Any

import jwt

from leave_assistant.application.errors import AuthenticationError
from leave_assistant.application.identity.claims import TokenClaims
from leave_assistant.infrastructure.identity.jwks import KeyResolver

ALLOWED_ALGORITHMS = ("RS256", "ES256")


class TokenVerifier:
    def __init__(self, keys: KeyResolver, *, issuer: str, audience: str, leeway_seconds: int = 30) -> None:
        self._keys = keys
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds

    async def verify(self, token: str) -> TokenClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("malformed token") from exc
        algorithm = header.get("alg")
        if algorithm not in ALLOWED_ALGORITHMS:
            raise AuthenticationError("unsupported token algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthenticationError("token has no key id")
        key = await self._keys.get_key(kid)
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                key=key.key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("token expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError("invalid audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError("invalid issuer") from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthenticationError("invalid signature") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid token") from exc
        return TokenClaims.from_payload(payload)
