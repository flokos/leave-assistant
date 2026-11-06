"""Deterministic citation verification (doc 04 §4 `verify_citations`): every [n] must refer to a retrieved source,
the answer must cite at least one source, and it may not contain URLs or invented references."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

MARKER = re.compile(r"\[(\d{1,2})\]")
URL = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
GROUPED = re.compile(r"\[\s*(\d{1,2}(?:\s*[,;]\s*\d{1,2})+)\s*\]")


@dataclass(frozen=True, slots=True)
class CitationCheck:
    ok: bool
    cited: tuple[int, ...]  # in order of first use, deduplicated
    problems: tuple[str, ...]


def markers_in(text: str) -> list[int]:
    seen: list[int] = []
    for match in MARKER.finditer(text):
        n = int(match.group(1))
        if n not in seen:
            seen.append(n)
    return seen


_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)


def strip_markdown(answer: str) -> str:
    """The chat shows plain text: bold/underline emphasis and heading marks are removed, the words kept."""
    return _HEADING.sub("", _EMPHASIS.sub(r"\2", answer))


def split_grouped(answer: str) -> str:
    """`[1, 2]` / `[1;2]` → `[1][2]`: models group markers although the prompt asks for one per bracket."""
    return GROUPED.sub(lambda m: "".join(f"[{n.strip()}]" for n in re.split(r"[,;]", m.group(1))), answer)


def attach_declared(answer: str, declared: Sequence[int], available: int) -> str:
    """Some models list their sources in the structured field but omit inline markers: when the text has none and
    every declared source exists, append them — the model named those sources itself; nothing is invented."""
    if markers_in(answer) or not declared or not answer.strip():
        return answer
    valid = [n for n in dict.fromkeys(declared) if 1 <= n <= available]
    if len(valid) != len(set(declared)):
        return answer
    return answer.rstrip() + " " + "".join(f"[{n}]" for n in valid)


def verify(answer: str, declared: Sequence[int], available: int) -> CitationCheck:
    """`declared` are the structured-output citations, `available` the number of sources in the prompt."""
    problems: list[str] = []
    inline = markers_in(answer)
    cited = list(dict.fromkeys([*inline, *declared]))
    unknown = [n for n in cited if not 1 <= n <= available]
    if unknown:
        problems.append(f"unknown sources {unknown}")
    if not inline:
        problems.append("no inline citation")
    if set(declared) - set(inline):
        problems.append("declared citations not used in the text")
    if URL.search(answer):
        problems.append("contains a URL")
    if not answer.strip():
        problems.append("empty answer")
    valid = tuple(n for n in cited if 1 <= n <= available)
    return CitationCheck(ok=not problems, cited=valid, problems=tuple(problems))


def renumber(answer: str, order: Sequence[int]) -> str:
    """Rewrite [n] markers so the cited sources are numbered 1..k in order of first use (what the UI shows)."""
    mapping = {old: new for new, old in enumerate(order, start=1)}
    return MARKER.sub(lambda m: f"[{mapping.get(int(m.group(1)), int(m.group(1)))}]", answer)
