"""Request correlation available to application services without importing the HTTP layer."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def current_request_id() -> str | None:
    return _request_id.get()


_origin: ContextVar[dict[str, Any] | None] = ContextVar("origin", default=None)


@contextmanager
def acting_via_agent(proposal_id: UUID) -> Iterator[None]:
    """Marks every request event and audit entry written inside the block as an agent execution of a confirmed
    proposal (doc 07 `via`, doc 08 §10). The human principal stays the actor; the proposal id makes the effect
    findable for exactly-once recovery (worklog M7)."""
    token = _origin.set({"via": "agent", "proposal_id": str(proposal_id)})
    try:
        yield
    finally:
        _origin.reset(token)


def current_origin() -> dict[str, Any] | None:
    return _origin.get()
