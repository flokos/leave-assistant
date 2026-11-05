"""LLM judge placeholder: the provider router arrives in M6; until then the judge reports "unavailable" (None)."""

from __future__ import annotations

from collections.abc import Sequence


class UnavailableLlmJudge:
    async def judge(self, excerpts: Sequence[str]) -> float | None:
        return None
