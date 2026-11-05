"""Cursor pagination (doc 09 §1): opaque keyset cursors over `(created_at, id)`."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from leave_assistant.application.errors import InvalidInputError

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1:
        raise InvalidInputError(
            "limit must be positive", errors=[{"loc": ["query", "limit"], "msg": "must be >= 1"}]
        )
    return min(limit, MAX_LIMIT)


def encode_cursor(sort_key: datetime, item_id: UUID) -> str:
    payload = json.dumps({"k": sort_key.isoformat(), "id": str(item_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return datetime.fromisoformat(data["k"]), UUID(data["id"])
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidInputError(
            "invalid cursor", errors=[{"loc": ["query", "cursor"], "msg": "malformed cursor"}]
        ) from exc


def build_page[T](rows: Sequence[T], limit: int, sort_key: Callable[[T], tuple[datetime, UUID]]) -> Page[T]:
    """`rows` was fetched with `limit + 1`; the extra row only signals that another page exists."""
    items = list(rows[:limit])
    next_cursor = None
    if len(rows) > limit and items:
        key, item_id = sort_key(items[-1])
        next_cursor = encode_cursor(key, item_id)
    return Page(items=items, next_cursor=next_cursor)
