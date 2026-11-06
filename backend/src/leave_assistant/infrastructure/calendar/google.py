"""Google Calendar v3 adapter (doc 10, ADR-0020): `google-auth-oauthlib` for consent and code exchange,
`google-auth` credentials (refresh in memory), `google-api-python-client` with the bundled discovery document.

The client libraries are synchronous; every call runs in a worker thread. Error classification (doc 10 §4.1):
`invalid_grant`/401 → AUTH_EXPIRED; 429, 5xx, rate-limit 403s and network errors → TRANSIENT (honouring
`Retry-After`); other 400/403/404 → PERMANENT; anything else → UNKNOWN. Messages carry status and reason only.

PKCE without stored secrets: the verifier is HMAC-SHA256(client secret, state), so only this server can recompute
it at the callback, and nothing beyond the hashed state is persisted (M8 decision 4).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from leave_assistant.application.calendar.models import (
    CalendarProviderError,
    CalendarTarget,
    OAuthGrant,
    ProviderEvent,
)
from leave_assistant.application.calendar.ports import EventExists
from leave_assistant.domain.calendar.model import APP_MARKER, ErrorClass

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})
HttpFactory = Callable[[], Any]  # tests inject `HttpMockSequence`; production uses the credentials' transport

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google may return a superset/subset; checked by us


def pkce_verifier(client_secret: str, state: str) -> str:
    digest = hmac.new(client_secret.encode(), b"pkce:" + state.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")  # 43 chars, the RFC 7636 minimum


def _reason(content: bytes) -> str:
    try:
        error = json.loads(content.decode() or "{}").get("error", {})
    except ValueError, UnicodeDecodeError:
        return ""
    if isinstance(error, str):
        return error
    errors = error.get("errors") or [{}]
    return str(errors[0].get("reason") or error.get("status") or "")


def classify(exc: BaseException) -> CalendarProviderError:
    """Map a client-library exception to a classified provider error."""
    from google.auth.exceptions import RefreshError, TransportError
    from googleapiclient.errors import HttpError

    if isinstance(exc, CalendarProviderError):
        return exc
    if isinstance(exc, RefreshError):
        text = str(exc)
        if "invalid_grant" in text or "invalid_client" in text or "unauthorized" in text.lower():
            return CalendarProviderError(ErrorClass.AUTH_EXPIRED, "invalid_grant: refresh token rejected")
        return CalendarProviderError(ErrorClass.TRANSIENT, "token refresh failed")
    if isinstance(exc, HttpError):
        status = int(exc.resp.status)
        reason = _reason(exc.content)
        retry_after = exc.resp.get("retry-after")
        delay = float(retry_after) if retry_after and str(retry_after).isdigit() else None
        message = f"{status} {reason}".strip()
        if status == 401:
            return CalendarProviderError(ErrorClass.AUTH_EXPIRED, message)
        if status == 429 or status >= 500 or (status == 403 and reason in RATE_LIMIT_REASONS):
            return CalendarProviderError(ErrorClass.TRANSIENT, message, retry_after=delay)
        if status in (400, 403, 404, 410):
            return CalendarProviderError(ErrorClass.PERMANENT, message)
        return CalendarProviderError(ErrorClass.UNKNOWN, message)
    if isinstance(exc, TransportError | OSError | TimeoutError):
        return CalendarProviderError(ErrorClass.TRANSIENT, f"network: {type(exc).__name__}")
    return CalendarProviderError(ErrorClass.UNKNOWN, type(exc).__name__)


def _status(exc: BaseException) -> int | None:
    from googleapiclient.errors import HttpError

    return int(exc.resp.status) if isinstance(exc, HttpError) else None


class GoogleCalendarProvider:
    name = "google"

    def __init__(
        self, client_id: str, client_secret: str, *, http_factory: HttpFactory | None = None
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http_factory = http_factory
        self._credentials: dict[str, Any] = {}  # refresh token → Credentials (access tokens stay in memory)

    def _service(self, target: CalendarTarget) -> Any:
        from googleapiclient.discovery import build

        if self._http_factory is not None:
            return build(
                "calendar", "v3", http=self._http_factory(), static_discovery=True, cache_discovery=False
            )
        from google.oauth2.credentials import Credentials

        creds = self._credentials.get(target.refresh_token)
        if creds is None:
            creds = Credentials(  # type: ignore[no-untyped-call]
                token=None,
                refresh_token=target.refresh_token,
                token_uri=TOKEN_URI,
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
            self._credentials[target.refresh_token] = creds
        return build("calendar", "v3", credentials=creds, static_discovery=True, cache_discovery=False)

    async def _run(self, target: CalendarTarget, call: Callable[[Any], Any]) -> Any:
        def work() -> Any:
            return call(self._service(target)).execute(num_retries=0)

        try:
            return await asyncio.to_thread(work)
        except Exception as exc:
            if _status(exc) in (401,):
                self._credentials.pop(target.refresh_token, None)
            raise

    async def create_calendar(self, target: CalendarTarget, summary: str) -> str:
        try:
            created = await self._run(target, lambda s: s.calendars().insert(body={"summary": summary}))
        except Exception as exc:
            raise classify(exc) from exc
        return str(created["id"])

    async def insert(self, target: CalendarTarget, body: dict[str, Any]) -> ProviderEvent:
        try:
            created = await self._run(
                target,
                lambda s: s.events().insert(calendarId=target.calendar_id, body=body, sendUpdates="none"),
            )
        except Exception as exc:
            if _status(exc) == 409:
                raise EventExists(str(body["id"])) from exc
            raise classify(exc) from exc
        return ProviderEvent(str(created["id"]), dict(created))

    async def get(self, target: CalendarTarget, event_id: str) -> ProviderEvent | None:
        try:
            found = await self._run(
                target, lambda s: s.events().get(calendarId=target.calendar_id, eventId=event_id)
            )
        except Exception as exc:
            if _status(exc) in (404, 410):
                return None
            raise classify(exc) from exc
        if found.get("status") == "cancelled":  # deleted events with client ids linger as "cancelled"
            return None
        return ProviderEvent(str(found["id"]), dict(found))

    async def patch(
        self, target: CalendarTarget, event_id: str, body: dict[str, Any]
    ) -> ProviderEvent | None:
        changes = {k: v for k, v in body.items() if k != "id"} | {"status": "confirmed"}
        try:
            updated = await self._run(
                target,
                lambda s: s.events().patch(
                    calendarId=target.calendar_id, eventId=event_id, body=changes, sendUpdates="none"
                ),
            )
        except Exception as exc:
            if _status(exc) in (404, 410):
                return None
            raise classify(exc) from exc
        return ProviderEvent(str(updated["id"]), dict(updated))

    async def delete(self, target: CalendarTarget, event_id: str) -> bool:
        try:
            await self._run(
                target,
                lambda s: s.events().delete(
                    calendarId=target.calendar_id, eventId=event_id, sendUpdates="none"
                ),
            )
        except Exception as exc:
            if _status(exc) in (404, 410):
                return False
            raise classify(exc) from exc
        return True

    async def list_app_events(
        self, target: CalendarTarget, *, time_min: datetime, time_max: datetime
    ) -> list[ProviderEvent]:
        out: list[ProviderEvent] = []
        token: str | None = None
        for _ in range(20):  # 20 × 250 events: far above this application's volume (doc 10 §8)

            def request(service: Any, page_token: str | None = token) -> Any:
                return service.events().list(
                    calendarId=target.calendar_id,
                    privateExtendedProperty=f"app={APP_MARKER}",
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    showDeleted=False,
                    maxResults=250,
                    pageToken=page_token,
                )

            try:
                page = await self._run(target, request)
            except Exception as exc:
                raise classify(exc) from exc
            out.extend(ProviderEvent(str(e["id"]), dict(e)) for e in page.get("items", []))
            token = page.get("nextPageToken")
            if not token:
                break
        return out


class GoogleOAuthClient:
    name = "google"

    def __init__(self, client_id: str, client_secret: str, *, http: httpx.AsyncClient | None = None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http

    def _flow(self, scopes: tuple[str, ...], redirect_uri: str, state: str) -> Any:
        from google_auth_oauthlib.flow import Flow

        config = {
            "web": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
                "redirect_uris": [redirect_uri],
            }
        }
        return Flow.from_client_config(
            config,
            scopes=list(scopes),
            redirect_uri=redirect_uri,
            state=state,
            code_verifier=pkce_verifier(self._client_secret, state),
            autogenerate_code_verifier=False,
        )

    def authorization_url(self, *, state: str, redirect_uri: str, scopes: tuple[str, ...]) -> str:
        url, _ = self._flow(scopes, redirect_uri, state).authorization_url(
            access_type="offline", prompt="consent", include_granted_scopes="false", state=state
        )
        return str(url)

    async def exchange(
        self, *, code: str, state: str, redirect_uri: str, scopes: tuple[str, ...]
    ) -> OAuthGrant:
        flow = self._flow(scopes, redirect_uri, state)
        try:
            token = await asyncio.to_thread(flow.fetch_token, code=code)
        except Exception as exc:
            raise CalendarProviderError(
                ErrorClass.PERMANENT, f"code exchange failed: {type(exc).__name__}"
            ) from exc
        refresh = token.get("refresh_token")
        if not refresh:
            raise CalendarProviderError(ErrorClass.PERMANENT, "no refresh token returned")
        granted = token.get("scope") or []
        scopes_granted = tuple(granted.split() if isinstance(granted, str) else granted)
        return OAuthGrant(refresh_token=str(refresh), scopes=scopes_granted)

    async def revoke(self, refresh_token: str) -> None:
        client = self._http or httpx.AsyncClient(timeout=10.0)
        try:
            response = await client.post(REVOKE_URI, data={"token": refresh_token})
        except httpx.HTTPError as exc:
            raise CalendarProviderError(ErrorClass.TRANSIENT, f"revoke: {type(exc).__name__}") from exc
        finally:
            if self._http is None:
                await client.aclose()
        if response.status_code >= 500:
            raise CalendarProviderError(ErrorClass.TRANSIENT, f"revoke: {response.status_code}")
