"""Department lookup by code or name for the availability tool (M10, doc 11 S19)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from leave_assistant.application.tenancy.lookup import resolve_department, stem
from leave_assistant.domain.tenancy.entities import Department

NOW = datetime(2026, 11, 7, tzinfo=UTC)


def dept(code: str, name: str) -> Department:
    return Department(id=uuid4(), tenant_id=uuid4(), code=code, name=name, created_at=NOW, updated_at=NOW)


AEGEAN = [
    dept("OPS", "Λειτουργίες"),
    dept("FIN", "Οικονομικές Υπηρεσίες"),
    dept("HR", "Ανθρώπινο Δυναμικό"),
    dept("WH", "Αποθήκη"),
]


def test_code_name_and_greek_inflections_resolve() -> None:
    fin = AEGEAN[1]
    assert resolve_department("fin", AEGEAN) is fin
    assert resolve_department("οικονομικες υπηρεσιες", AEGEAN) is fin  # accents and case folded
    assert (
        resolve_department("τμήμα Οικονομικών Υπηρεσιών", AEGEAN) is fin
    )  # genitive plural, extra word (S19)
    assert resolve_department("Ανθρώπινου Δυναμικού", AEGEAN) is AEGEAN[2]  # genitive singular
    assert resolve_department("Αποθήκης", AEGEAN) is AEGEAN[3]
    assert resolve_department("Οικονομικών", AEGEAN) is fin  # one distinctive word of the name
    assert resolve_department("τμήμα", AEGEAN) is None  # a generic word alone matches nothing


def test_english_plurals_and_unknown_or_ambiguous_names() -> None:
    brightwater = [dept("ENG", "Engineering"), dept("PROD", "Product"), dept("PPL", "People")]
    assert resolve_department("the Engineering team", brightwater) is brightwater[0]
    assert resolve_department("Products", brightwater) is brightwater[1]
    assert resolve_department("Marketing", brightwater) is None
    assert resolve_department("", brightwater) is None
    twins = [dept("S1", "Sales North"), dept("S2", "Sales")]
    assert resolve_department("Sales North", twins) is twins[0]  # exact name first
    assert resolve_department("North Sales team", twins) is None  # both match by stems → no guess
    assert resolve_department("Sales", twins) is twins[1]  # exact name
    regions = [dept("SN", "Sales North"), dept("SS", "Sales South")]
    assert resolve_department("North", regions) is regions[0]  # one distinctive word, one department
    assert resolve_department("sales", regions) is None  # a word shared by two departments → no guess


def test_stems_keep_a_minimum_length() -> None:
    assert stem("Υπηρεσιών") == stem("Υπηρεσίες") == "υπηρεσι"
    assert stem("ος") == "οσ"  # too short to strip
