"""Grounding check for business answers (M7): every number the model writes must come from this turn's tool results or
from the user's own message. Citation markers and placeholders are not numbers; "20", "20.0" and "20,0" are equal."""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from leave_assistant.domain.agent.citations import MARKER
from leave_assistant.domain.agent.streaming import PLACEHOLDER

NUMBER = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?!\w)")


def _normalise(token: str) -> str:
    try:
        value = Decimal(token.replace(",", "."))
    except InvalidOperation:  # pragma: no cover - the pattern only matches digits
        return token
    text = format(value.normalize(), "f")
    return text


def numbers_in(text: str) -> set[str]:
    plain = PLACEHOLDER.sub(" ", MARKER.sub(" ", text))
    return {_normalise(m.group(1)) for m in NUMBER.finditer(plain)}


def ungrounded_numbers(answer: str, sources: Iterable[str]) -> set[str]:
    """Numbers in `answer` that occur in none of `sources` (empty set = grounded)."""
    known: set[str] = set()
    for source in sources:
        known |= numbers_in(source)
    return numbers_in(answer) - known
