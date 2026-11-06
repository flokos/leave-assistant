"""Provider ports (doc 10): the calendar API and the OAuth client. Adapters: mock (default) and Google."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from leave_assistant.application.calendar.models import (
    CalendarProviderError,
    CalendarTarget,
    OAuthGrant,
    ProviderEvent,
)
from leave_assistant.domain.calendar.model import ConnectionKind, ErrorClass

SCOPES: dict[ConnectionKind, tuple[str, ...]] = {
    ConnectionKind.COMPANY_SHARED: ("https://www.googleapis.com/auth/calendar.app.created",),
    ConnectionKind.PERSONAL: ("https://www.googleapis.com/auth/calendar.events.owned",),
}
FALLBACK_SCOPES: dict[ConnectionKind, tuple[str, ...]] = {
    ConnectionKind.COMPANY_SHARED: ("https://www.googleapis.com/auth/calendar",),
    ConnectionKind.PERSONAL: ("https://www.googleapis.com/auth/calendar.events",),
}


class CalendarProvider(Protocol):
    """Every method raises `CalendarProviderError` with a classified error; ids are deterministic (doc 10 §3)."""

    name: str

    async def create_calendar(self, target: CalendarTarget, summary: str) -> str: ...
    async def insert(self, target: CalendarTarget, body: dict[str, Any]) -> ProviderEvent:
        """Create; an existing event with the same id raises `EventExists`."""
        ...

    async def get(self, target: CalendarTarget, event_id: str) -> ProviderEvent | None: ...
    async def patch(
        self, target: CalendarTarget, event_id: str, body: dict[str, Any]
    ) -> ProviderEvent | None:
        """Update; `None` when the event does not exist."""
        ...

    async def delete(self, target: CalendarTarget, event_id: str) -> bool:
        """`False` when the event was already gone (404/410) — still a success."""
        ...

    async def list_app_events(
        self, target: CalendarTarget, *, time_min: datetime, time_max: datetime
    ) -> list[ProviderEvent]: ...


class OAuthClient(Protocol):
    name: str

    def authorization_url(self, *, state: str, redirect_uri: str, scopes: tuple[str, ...]) -> str: ...
    async def exchange(
        self, *, code: str, state: str, redirect_uri: str, scopes: tuple[str, ...]
    ) -> OAuthGrant: ...
    async def revoke(self, refresh_token: str) -> None: ...


class EventExists(Exception):
    """`events.insert` answered 409: an event with this id is already in the calendar."""


class UnconfiguredCalendar:
    """Neither provider configured: no connection can be made, so no sync job is ever created."""

    name = "none"

    def _fail(self) -> CalendarProviderError:
        return CalendarProviderError(ErrorClass.PERMANENT, "no calendar provider is configured")

    async def create_calendar(self, target: CalendarTarget, summary: str) -> str:
        raise self._fail()

    async def insert(self, target: CalendarTarget, body: dict[str, Any]) -> ProviderEvent:
        raise self._fail()

    async def get(self, target: CalendarTarget, event_id: str) -> ProviderEvent | None:
        raise self._fail()

    async def patch(
        self, target: CalendarTarget, event_id: str, body: dict[str, Any]
    ) -> ProviderEvent | None:
        raise self._fail()

    async def delete(self, target: CalendarTarget, event_id: str) -> bool:
        raise self._fail()

    async def list_app_events(
        self, target: CalendarTarget, *, time_min: datetime, time_max: datetime
    ) -> list[ProviderEvent]:
        raise self._fail()

    def authorization_url(self, *, state: str, redirect_uri: str, scopes: tuple[str, ...]) -> str:
        raise self._fail()

    async def exchange(
        self, *, code: str, state: str, redirect_uri: str, scopes: tuple[str, ...]
    ) -> OAuthGrant:
        raise self._fail()

    async def revoke(self, refresh_token: str) -> None:
        return None


@dataclass(frozen=True, slots=True)
class CalendarAdapters:
    provider: CalendarProvider
    oauth: OAuthClient
    redirect_uri: str = "http://localhost:8080/api/v1/calendar/connections/google/callback"


def unconfigured() -> CalendarAdapters:
    none = UnconfiguredCalendar()
    return CalendarAdapters(provider=none, oauth=none)
