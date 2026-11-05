"""Placeholders, findings and the `LlmSafeText` type (ADR-0017).

`LlmSafeText` is a distinct `str` subclass: prompt builders, state and traces (M6+) accept only it, so a raw string
reaching an LLM is a type error for mypy and an assertion at runtime.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class EntityType(StrEnum):
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    AMKA = "AMKA"
    AFM = "AFM"
    ID_CARD = "ID_CARD"
    IBAN = "IBAN"
    ADDRESS = "ADDRESS"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    OTHER = "OTHER"


PLACEHOLDER_RE = re.compile(
    r"\b(PERSON|EMAIL|PHONE|AMKA|AFM|ID_CARD|IBAN|ADDRESS|DATE_OF_BIRTH|OTHER)_(\d{4,6})\b"
)


def format_placeholder(entity_type: EntityType, seq: int) -> str:
    if seq < 0:
        raise ValueError("placeholder sequence must be non-negative")
    return f"{entity_type.value}_{seq:04d}"


def parse_placeholder(token: str) -> tuple[EntityType, int] | None:
    match = PLACEHOLDER_RE.fullmatch(token.strip())
    if match is None:
        return None
    return EntityType(match.group(1)), int(match.group(2))


@dataclass(frozen=True, slots=True)
class PlaceholderSpan:
    start: int
    end: int
    placeholder: str
    entity_type: EntityType
    seq: int


def find_placeholders(text: str) -> list[PlaceholderSpan]:
    return [
        PlaceholderSpan(m.start(), m.end(), m.group(0), EntityType(m.group(1)), int(m.group(2)))
        for m in PLACEHOLDER_RE.finditer(text)
    ]


class LlmSafeText(str):
    """Pseudonymised text. Construct only through the pseudonymisation service (or tests)."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Finding:
    """A detected PII span. `source` tells which layer found it (known_entity beats presidio beats pattern)."""

    entity_type: EntityType
    start: int
    end: int
    score: float
    source: str
    text: str
    subject_employee_id: UUID | None = None  # set by the known-entity layer


SOURCE_PRIORITY = {"known_entity": 1}  # the directory layer always wins; other layers compete on score/length


def merge_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Drop overlapping spans: the stronger source wins, then the higher score, then the longer span."""
    ordered = sorted(
        findings,
        key=lambda f: (-SOURCE_PRIORITY.get(f.source, 0), -f.score, -(f.end - f.start), f.start),
    )
    kept: list[Finding] = []
    for finding in ordered:
        if finding.end <= finding.start:
            continue
        if any(not (finding.end <= k.start or finding.start >= k.end) for k in kept):
            continue
        kept.append(finding)
    return sorted(kept, key=lambda f: f.start)


def apply_replacements(text: str, replacements: Sequence[tuple[int, int, str]]) -> str:
    """Replace non-overlapping `(start, end, replacement)` spans, right to left so offsets stay valid."""
    out = text
    for start, end, replacement in sorted(replacements, key=lambda r: r[0], reverse=True):
        if start < 0 or end > len(text) or start >= end:
            raise ValueError(f"invalid span {start}:{end}")
        out = out[:start] + replacement + out[end:]
    return out


@dataclass(frozen=True, slots=True)
class PlaceholderOccurrence:
    """Provenance of one placeholder in one text (doc 08 §7.4 needs it to decide re-identification)."""

    placeholder: str
    entity_type: EntityType
    source_kind: str  # user_message | document_version | tool_result
    source_id: str | None = None
    subject_employee_id: UUID | None = None
