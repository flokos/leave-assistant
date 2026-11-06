"""Google Calendar adapter with recorded responses (`googleapiclient.http.HttpMockSequence`; doc 10 §6 — the
library's own HTTP mock replaces respx, which only intercepts httpx)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest

pytest.importorskip("googleapiclient")
from googleapiclient.http import HttpMockSequence  # noqa: E402

from leave_assistant.application.calendar.models import CalendarProviderError, CalendarTarget  # noqa: E402
from leave_assistant.application.calendar.ports import SCOPES, EventExists  # noqa: E402
from leave_assistant.domain.calendar.model import ConnectionKind, ErrorClass  # noqa: E402
from leave_assistant.infrastructure.calendar.google import (  # noqa: E402
    GoogleCalendarProvider,
    GoogleOAuthClient,
    classify,
    pkce_verifier,
)

TARGET = CalendarTarget(uuid4(), uuid4(), "cal@group.calendar.google.com", "refresh")
BODY = {
    "id": "laabc",
    "summary": "Out of office",
    "start": {"date": "2026-12-14"},
    "end": {"date": "2026-12-19"},
}


def _error(status: int, reason: str = "", **headers: str) -> tuple[dict[str, str], bytes]:
    content = json.dumps(
        {"error": {"code": status, "errors": [{"reason": reason}], "message": reason}}
    ).encode()
    return {"status": str(status), **headers}, content


def _ok(payload: dict[str, object] | None = None, status: int = 200) -> tuple[dict[str, str], bytes]:
    return {"status": str(status)}, json.dumps(payload or {}).encode()


class Recorder:
    def __init__(self, responses: list[tuple[dict[str, str], bytes]]) -> None:
        self.http = HttpMockSequence(responses)
        self.requests: list[tuple[str, str, object]] = []
        original = self.http.request

        def request(uri: str, method: str = "GET", body: object = None, headers: object = None, **kw: object):  # type: ignore[no-untyped-def]
            self.requests.append((method, uri, body))
            return original(uri, method=method, body=body, headers=headers, **kw)

        self.http.request = request  # type: ignore[method-assign]


def provider(rec: Recorder) -> GoogleCalendarProvider:
    return GoogleCalendarProvider("client", "secret", http_factory=lambda: rec.http)


async def test_insert_maps_the_call_and_409_means_exists() -> None:
    rec = Recorder([_ok(BODY), _error(409, "duplicate")])
    google = provider(rec)
    event = await google.insert(TARGET, BODY)
    assert event.event_id == "laabc"
    method, uri, body = rec.requests[0]
    assert method == "POST" and "/calendars/cal%40group.calendar.google.com/events" in uri
    assert "sendUpdates=none" in uri and json.loads(str(body))["id"] == "laabc"
    with pytest.raises(EventExists):
        await google.insert(TARGET, BODY)


async def test_patch_get_delete_handle_missing_events() -> None:
    rec = Recorder(
        [
            _ok(BODY),
            _error(404, "notFound"),
            _ok(BODY | {"status": "cancelled"}),
            _error(410, "deleted"),
            _ok(status=204),
            _error(404, "notFound"),
        ]
    )
    google = provider(rec)
    assert (await google.patch(TARGET, "laabc", BODY)) is not None
    assert json.loads(str(rec.requests[0][2]))["status"] == "confirmed" and "id" not in json.loads(
        str(rec.requests[0][2])
    )
    assert await google.patch(TARGET, "laabc", BODY) is None
    assert await google.get(TARGET, "laabc") is None  # cancelled = gone
    assert await google.get(TARGET, "laabc") is None
    assert await google.delete(TARGET, "laabc") is True
    assert await google.delete(TARGET, "laabc") is False  # already deleted → success


async def test_list_filters_by_the_app_marker_and_pages() -> None:
    rec = Recorder(
        [_ok({"items": [BODY], "nextPageToken": "p2"}), _ok({"items": [BODY | {"id": "lasecond"}]})]
    )
    events = await provider(rec).list_app_events(
        TARGET, time_min=datetime(2026, 10, 1, tzinfo=UTC), time_max=datetime(2027, 11, 1, tzinfo=UTC)
    )
    assert [e.event_id for e in events] == ["laabc", "lasecond"]
    first = parse_qs(urlparse(rec.requests[0][1]).query)
    assert first["privateExtendedProperty"] == ["app=leave-assistant"] and first["singleEvents"] == ["true"]
    assert parse_qs(urlparse(rec.requests[1][1]).query)["pageToken"] == ["p2"]


async def test_create_calendar_returns_its_id() -> None:
    rec = Recorder([_ok({"id": "new@group.calendar.google.com", "summary": "Leave — Aegean"})])
    assert await provider(rec).create_calendar(TARGET, "Leave — Aegean") == "new@group.calendar.google.com"
    assert json.loads(str(rec.requests[0][2])) == {"summary": "Leave — Aegean"}


@pytest.mark.parametrize(
    ("status", "reason", "headers", "expected", "retry_after"),
    [
        (500, "backendError", {}, ErrorClass.TRANSIENT, None),
        (503, "backendError", {"retry-after": "30"}, ErrorClass.TRANSIENT, 30.0),
        (429, "rateLimitExceeded", {}, ErrorClass.TRANSIENT, None),
        (403, "userRateLimitExceeded", {}, ErrorClass.TRANSIENT, None),
        (403, "insufficientPermissions", {}, ErrorClass.PERMANENT, None),
        (400, "invalid", {}, ErrorClass.PERMANENT, None),
        (404, "notFound", {}, ErrorClass.PERMANENT, None),
        (401, "authError", {}, ErrorClass.AUTH_EXPIRED, None),
        (418, "teapot", {}, ErrorClass.UNKNOWN, None),
    ],
)
async def test_error_classification(
    status: int, reason: str, headers: dict[str, str], expected: ErrorClass, retry_after: float | None
) -> None:
    rec = Recorder([_error(status, reason, **headers)])
    with pytest.raises(CalendarProviderError) as caught:
        await provider(rec).insert(TARGET, BODY)
    assert caught.value.error_class is expected and caught.value.retry_after == retry_after
    assert reason in str(caught.value) and "Out of office" not in str(caught.value)  # no payload in messages


def test_refresh_and_network_errors() -> None:
    from google.auth.exceptions import RefreshError, TransportError

    grant = classify(RefreshError("invalid_grant: Token has been expired or revoked."))
    assert grant.error_class is ErrorClass.AUTH_EXPIRED
    assert classify(TransportError("connection reset")).error_class is ErrorClass.TRANSIENT
    assert classify(TimeoutError()).error_class is ErrorClass.TRANSIENT
    assert classify(ValueError("odd")).error_class is ErrorClass.UNKNOWN


def test_consent_url_requests_offline_access_with_pkce() -> None:
    client = GoogleOAuthClient("client-id.apps.googleusercontent.com", "s3cret")
    url = client.authorization_url(
        state="nonce123", redirect_uri="http://localhost:8080/cb", scopes=SCOPES[ConnectionKind.PERSONAL]
    )
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/auth?")
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["state"] == ["nonce123"] and query["redirect_uri"] == ["http://localhost:8080/cb"]
    assert query["scope"] == ["https://www.googleapis.com/auth/calendar.events.owned"]
    assert query["code_challenge_method"] == ["S256"] and "s3cret" not in url
    verifier = pkce_verifier("s3cret", "nonce123")
    assert len(verifier) == 43 and verifier == pkce_verifier("s3cret", "nonce123")
    assert verifier != pkce_verifier("other", "nonce123")


async def test_revoke_posts_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await GoogleOAuthClient("id", "secret", http=http).revoke("rt-1")
    assert seen[0].url == "https://oauth2.googleapis.com/revoke" and seen[0].content == b"token=rt-1"
