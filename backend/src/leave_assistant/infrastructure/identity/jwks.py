"""JWKS cache for Keycloak signing keys (doc 08 §4): TTL refresh, forced refresh on unknown `kid` (rate-limited)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Protocol

import httpx
import jwt

from leave_assistant.application.errors import AuthenticationError, DependencyError


class KeyResolver(Protocol):
    async def get_key(self, kid: str) -> jwt.PyJWK: ...


class StaticKeys:
    """Preloaded keys (tests, offline verification)."""

    def __init__(self, keys: list[jwt.PyJWK]) -> None:
        self._keys = {k.key_id: k for k in keys if k.key_id}

    async def get_key(self, kid: str) -> jwt.PyJWK:
        try:
            return self._keys[kid]
        except KeyError as exc:
            raise AuthenticationError("unknown signing key") from exc


class JwksCache:
    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: float = 600.0,
        min_refresh_interval: float = 10.0,
        timeout: float = 5.0,
        http: httpx.AsyncClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._min_refresh = min_refresh_interval
        self._timeout = timeout
        self._http = http
        self._clock = monotonic
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        return self._url

    def _expired(self) -> bool:
        return self._fetched_at is None or self._clock() - self._fetched_at > self._ttl

    async def refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            if not force and not self._expired():
                return
            if (
                self._fetched_at is not None
                and self._clock() - self._fetched_at < self._min_refresh
                and not self._expired()
            ):
                return
            try:
                if self._http is None:
                    async with httpx.AsyncClient(timeout=self._timeout) as http:
                        response = await http.get(self._url)
                else:
                    response = await self._http.get(self._url)
                response.raise_for_status()
                key_set = jwt.PyJWKSet.from_dict(response.json())
            except (httpx.HTTPError, ValueError, jwt.PyJWKSetError, jwt.PyJWKError) as exc:
                if self._keys:  # keep serving cached keys while the IdP is unreachable
                    return
                raise DependencyError("identity provider keys unavailable") from exc
            self._keys = {k.key_id: k for k in key_set.keys if k.key_id}
            self._fetched_at = self._clock()

    async def get_key(self, kid: str) -> jwt.PyJWK:
        if self._expired():
            await self.refresh()
        key = self._keys.get(kid)
        if key is None:
            await self._refresh_for_unknown_kid()
            key = self._keys.get(kid)
        if key is None:
            raise AuthenticationError("unknown signing key")
        return key

    async def _refresh_for_unknown_kid(self) -> None:
        # Unknown kids trigger at most one refresh per `min_refresh_interval` (key rotation vs. forged-kid floods).
        if self._fetched_at is not None and self._clock() - self._fetched_at < self._min_refresh:
            return
        await self.refresh(force=True)
