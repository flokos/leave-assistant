from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from leave_assistant.application.errors import InvalidInputError
from leave_assistant.application.pagination import build_page, clamp_limit, decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    key = (datetime(2026, 11, 5, 9, 0, tzinfo=UTC), UUID(int=7))
    assert decode_cursor(encode_cursor(*key)) == key
    assert decode_cursor(None) is None
    assert decode_cursor("") is None


@pytest.mark.parametrize("bad", ["@@@", "eyJrIjoieCJ9", "bm90LWpzb24"])
def test_invalid_cursor(bad: str) -> None:
    with pytest.raises(InvalidInputError):
        decode_cursor(bad)


def test_clamp_limit() -> None:
    assert clamp_limit(None) == 50
    assert clamp_limit(500) == 200
    with pytest.raises(InvalidInputError):
        clamp_limit(0)


def test_build_page_signals_next_page_only_when_more_rows() -> None:
    rows = [(datetime(2026, 1, 1, tzinfo=UTC), UUID(int=i)) for i in range(4)]
    page = build_page(rows, 3, lambda r: r)
    assert len(page.items) == 3 and page.next_cursor is not None
    assert decode_cursor(page.next_cursor) == rows[2]
    assert build_page(rows[:3], 3, lambda r: r).next_cursor is None
