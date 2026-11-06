"""Streaming re-identification (doc 09 §3): a buffered matcher holds back any suffix that could still become a
placeholder (`PERSON_0042`) until it is complete or disambiguated, so a token boundary never splits one."""

from __future__ import annotations

import re
from collections.abc import Callable

PLACEHOLDER = re.compile(r"\b[A-Z][A-Z_]*_\d{4}\b")
# a suffix that may still grow into a placeholder: capitals/underscores, optionally followed by up to 4 digits
_PARTIAL = re.compile(r"[A-Z][A-Z_]*(?:_\d{0,4})?$")


class BufferedReidentifier:
    def __init__(self, resolve: Callable[[str], str]) -> None:
        self._resolve = resolve
        self._buffer = ""

    def _replace(self, text: str) -> str:
        return PLACEHOLDER.sub(lambda m: self._resolve(m.group(0)), text)

    def feed(self, chunk: str) -> str:
        """Returns the text that is safe to emit now (re-identified)."""
        self._buffer += chunk
        match = _PARTIAL.search(self._buffer)
        hold = 0
        if match is not None:
            start = match.start()
            # only hold a suffix that begins at a word boundary
            if start == 0 or not (self._buffer[start - 1].isalnum() or self._buffer[start - 1] == "_"):
                hold = len(self._buffer) - start
        ready, self._buffer = (
            self._buffer[: len(self._buffer) - hold],
            self._buffer[len(self._buffer) - hold :],
        )
        return self._replace(ready)

    def flush(self) -> str:
        rest, self._buffer = self._buffer, ""
        return self._replace(rest)


def reidentify_all(text: str, resolve: Callable[[str], str]) -> str:
    return PLACEHOLDER.sub(lambda m: resolve(m.group(0)), text)
