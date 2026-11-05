"""Known-entity recogniser (doc 08 §7.1): deterministic matches of the tenant's employees — names in every Greek case,
initials, e-mails. It does not depend on NER confidence, which is why it is the strongest layer."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from leave_assistant.application.ports import KnownEntity
from leave_assistant.domain.privacy.normalise import fold, name_variants, tokens_with_offsets
from leave_assistant.domain.privacy.types import EntityType, Finding

MIN_SURNAME_CHARS = 3
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
POSSESSIVE = re.compile(r"['’]s$")


@dataclass(frozen=True, slots=True)
class _Index:
    by_last: dict[
        str, list[tuple[KnownEntity, set[str], set[str]]]
    ]  # folded surname variant → (entity, first variants, initials)
    by_email: dict[str, KnownEntity]


def build_index(known: Sequence[KnownEntity]) -> _Index:
    by_last: dict[str, list[tuple[KnownEntity, set[str], set[str]]]] = {}
    by_email: dict[str, KnownEntity] = {}
    for entity in known:
        variants = name_variants(entity.first_name, entity.last_name)
        for last in variants["last"]:
            if len(last) >= MIN_SURNAME_CHARS:
                by_last.setdefault(last, []).append((entity, variants["first"], variants["initials"]))
        if entity.email:
            by_email[fold(entity.email)] = entity
    return _Index(by_last, by_email)


def known_entity_findings(text: str, known: Sequence[KnownEntity]) -> list[Finding]:
    if not known or not text:
        return []
    index = build_index(known)
    findings: list[Finding] = []
    for match in EMAIL_RE.finditer(text):  # e-mails are not word tokens: matched on the raw text
        email = index.by_email.get(fold(match.group(0)))
        if email is not None:
            findings.append(
                Finding(
                    EntityType.EMAIL,
                    match.start(),
                    match.end(),
                    1.0,
                    "known_entity",
                    match.group(0),
                    email.employee_id,
                )
            )
    tokens = tokens_with_offsets(text)
    taken: set[int] = set()
    for position, (start, end, token) in enumerate(tokens):
        folded = POSSESSIVE.sub("", fold(token))  # "Byrne's" → "byrne"
        candidates = index.by_last.get(folded)
        if not candidates or position in taken:
            continue
        chosen: tuple[KnownEntity, int] | None = None
        if position > 0:
            prev_start, _, prev_token = tokens[position - 1]
            prev_folded = fold(prev_token)
            for entity, firsts, initials in candidates:
                if prev_folded in firsts or prev_folded in initials:
                    chosen = (entity, prev_start)
                    break
        if chosen is None:
            entity = candidates[0][
                0
            ]  # surname alone: still a known employee (ambiguity resolved to the first match)
            chosen = (entity, start)
        entity, span_start = chosen
        findings.append(
            Finding(
                EntityType.PERSON,
                span_start,
                end,
                1.0,
                "known_entity",
                text[span_start:end],
                entity.employee_id,
            )
        )
        taken.add(position)
    return findings
