"""Mock calendar provider (doc 10 §6): `app.mock_calendar_events`, the same semantics as Google (deterministic
ids, 409 on duplicate inserts, 404/410 deletes are successes), simulated consent, and failure injection.

Fail modes (`MOCK_CALENDAR_FAIL_MODE`):
- `none`
- `transient_once` — the first call per (event, operation) fails with a 503, the retry succeeds
- `auth_expired` — refresh tokens issued before this process started are rejected (`invalid_grant`); a
  re-authorisation issues a fresh token, so "expire → park → re-authorise → resume" works without restarting
- `permanent` — every write fails with a 403
"""

from __future__ import annotations

import secrets
import time
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlencode

from leave_assistant.application.calendar.models import (
    CalendarProviderError,
    CalendarTarget,
    OAuthGrant,
    ProviderEvent,
)
from leave_assistant.application.calendar.ports import EventExists
from leave_assistant.application.ports import Clock, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.calendar.model import ErrorClass

FailMode = Literal["none", "transient_once", "auth_expired", "permanent"]
TOKEN_PREFIX = "mock-rt"
CODE_PREFIX = "mock-code-"


def issue_token(now_ms: int | None = None) -> str:
    return (
        f"{TOKEN_PREFIX}:{now_ms if now_ms is not None else int(time.time() * 1000)}:{secrets.token_hex(8)}"
    )


def token_issued_ms(token: str) -> int | None:
    parts = token.split(":")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


class MockCalendarProvider:
    """Implements both `CalendarProvider` and `OAuthClient`."""

    name = "mock"

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        *,
        fail_mode: FailMode = "none",
        valid_after_ms: int | None = None,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self.fail_mode: FailMode = fail_mode
        self.valid_after_ms = valid_after_ms if valid_after_ms is not None else int(time.time() * 1000)
        self._failed_once: set[tuple[str, str]] = set()
        self.calls: list[tuple[str, str]] = []  # (operation, event id) — for tests

    # -- OAuth ---------------------------------------------------------------------------------------------------
    def authorization_url(self, *, state: str, redirect_uri: str, scopes: tuple[str, ...]) -> str:
        """Simulated consent: straight back to the callback with a code, as if the user clicked "Allow"."""
        query = urlencode(
            {"state": state, "code": CODE_PREFIX + secrets.token_hex(8), "scope": " ".join(scopes)}
        )
        return f"{redirect_uri}?{query}"

    async def exchange(
        self, *, code: str, state: str, redirect_uri: str, scopes: tuple[str, ...]
    ) -> OAuthGrant:
        if not code.startswith(CODE_PREFIX):
            raise CalendarProviderError(ErrorClass.PERMANENT, "invalid_grant: unknown authorisation code")
        return OAuthGrant(refresh_token=issue_token(), scopes=scopes)

    async def revoke(self, refresh_token: str) -> None:
        return None

    # -- calendar ------------------------------------------------------------------------------------------------
    def _guard(
        self, target: CalendarTarget, operation: str, event_id: str = "", *, write: bool = True
    ) -> None:
        self.calls.append((operation, event_id))
        if self.fail_mode == "auth_expired":
            issued = token_issued_ms(target.refresh_token)
            if issued is None or issued < self.valid_after_ms:
                raise CalendarProviderError(
                    ErrorClass.AUTH_EXPIRED, "invalid_grant: token has been expired or revoked"
                )
        if self.fail_mode == "permanent" and write:
            raise CalendarProviderError(ErrorClass.PERMANENT, "403 forbidden: insufficient permissions")
        if self.fail_mode == "transient_once" and write and (operation, event_id) not in self._failed_once:
            self._failed_once.add((operation, event_id))
            raise CalendarProviderError(ErrorClass.TRANSIENT, "503 backend error", retry_after=None)

    def _ctx(self, target: CalendarTarget) -> TenantContext:
        return TenantContext(tenant_id=target.tenant_id, user_id="calendar.mock", roles=("system",))

    async def create_calendar(self, target: CalendarTarget, summary: str) -> str:
        self._guard(target, "create_calendar", write=False)
        return f"mock-cal-{target.connection_id}"

    async def insert(self, target: CalendarTarget, body: dict[str, Any]) -> ProviderEvent:
        event_id = str(body["id"])
        self._guard(target, "insert", event_id)
        async with self._uow(self._ctx(target)) as uow:
            created = await uow.mock_calendar.put(target.calendar_id, event_id, body, self._clock.now())
            await uow.commit()
        if not created:
            raise EventExists(event_id)
        return ProviderEvent(event_id, body)

    async def get(self, target: CalendarTarget, event_id: str) -> ProviderEvent | None:
        self._guard(target, "get", event_id, write=False)
        async with self._uow(self._ctx(target)) as uow:
            body = await uow.mock_calendar.get(target.calendar_id, event_id)
        return ProviderEvent(event_id, body) if body is not None else None

    async def patch(
        self, target: CalendarTarget, event_id: str, body: dict[str, Any]
    ) -> ProviderEvent | None:
        self._guard(target, "patch", event_id)
        async with self._uow(self._ctx(target)) as uow:
            current = await uow.mock_calendar.get(target.calendar_id, event_id)
            if current is None:
                return None
            merged = current | {k: v for k, v in body.items() if k != "id"}
            await uow.mock_calendar.replace(target.calendar_id, event_id, merged, self._clock.now())
            await uow.commit()
        return ProviderEvent(event_id, merged)

    async def delete(self, target: CalendarTarget, event_id: str) -> bool:
        self._guard(target, "delete", event_id)
        async with self._uow(self._ctx(target)) as uow:
            removed = await uow.mock_calendar.remove(target.calendar_id, event_id)
            await uow.commit()
        return removed

    async def list_app_events(
        self, target: CalendarTarget, *, time_min: datetime, time_max: datetime
    ) -> list[ProviderEvent]:
        self._guard(target, "list", write=False)
        async with self._uow(self._ctx(target)) as uow:
            bodies = await uow.mock_calendar.list(target.calendar_id)
        out: list[ProviderEvent] = []
        for body in bodies:
            start = date.fromisoformat(str((body.get("start") or {}).get("date", "1970-01-01")))
            end = date.fromisoformat(str((body.get("end") or {}).get("date", "1970-01-01")))
            if end > time_min.date() and start < time_max.date():
                out.append(ProviderEvent(str(body["id"]), body))
        return out
