"""Request correlation available to application services without importing the HTTP layer."""

from __future__ import annotations

from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def current_request_id() -> str | None:
    return _request_id.get()
