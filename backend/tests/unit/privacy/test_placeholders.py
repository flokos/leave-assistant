"""Placeholder grammar, finding merge and replacement mechanics (doc 08 §7.2)."""

from __future__ import annotations

import pytest

from leave_assistant.domain.privacy.types import (
    EntityType,
    Finding,
    LlmSafeText,
    apply_replacements,
    find_placeholders,
    format_placeholder,
    merge_findings,
    parse_placeholder,
)


def test_grammar_round_trip() -> None:
    assert format_placeholder(EntityType.PERSON, 42) == "PERSON_0042"
    assert format_placeholder(EntityType.AMKA, 12345) == "AMKA_12345"
    assert parse_placeholder("EMAIL_0007") == (EntityType.EMAIL, 7)
    assert parse_placeholder("person_0007") is None and parse_placeholder("PERSON_7") is None
    with pytest.raises(ValueError):
        format_placeholder(EntityType.PERSON, -1)


def test_find_placeholders_in_text() -> None:
    spans = find_placeholders("PERSON_0001 asked PERSON_0002 (EMAIL_0003) about AFM_0010; not PERSONX_0001.")
    assert [(s.placeholder, s.entity_type, s.seq) for s in spans] == [
        ("PERSON_0001", EntityType.PERSON, 1),
        ("PERSON_0002", EntityType.PERSON, 2),
        ("EMAIL_0003", EntityType.EMAIL, 3),
        ("AFM_0010", EntityType.AFM, 10),
    ]


def test_llm_safe_text_is_a_distinct_type() -> None:
    safe = LlmSafeText("PERSON_0001 is on leave")
    assert isinstance(safe, str) and isinstance(safe, LlmSafeText) and not isinstance("raw", LlmSafeText)


def test_merge_prefers_known_entities_then_score_then_length() -> None:
    findings = [
        Finding(EntityType.PERSON, 0, 14, 0.85, "presidio", "Μαρία Οικονόμου"),
        Finding(EntityType.PERSON, 6, 14, 1.0, "known_entity", "Οικονόμου"),
        Finding(EntityType.EMAIL, 20, 40, 1.0, "presidio", "maria@example.com"),
        Finding(EntityType.OTHER, 30, 35, 0.3, "pattern", "examp"),
        Finding(EntityType.OTHER, 50, 50, 0.9, "pattern", ""),
    ]
    merged = merge_findings(findings)
    assert [(f.source, f.start, f.end) for f in merged] == [("known_entity", 6, 14), ("presidio", 20, 40)]


def test_apply_replacements_right_to_left_and_validation() -> None:
    text = "Alice met Bob"
    assert (
        apply_replacements(text, [(0, 5, "PERSON_0001"), (10, 13, "PERSON_0002")])
        == "PERSON_0001 met PERSON_0002"
    )
    with pytest.raises(ValueError):
        apply_replacements(text, [(5, 2, "x")])
