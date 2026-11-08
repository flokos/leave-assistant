"""Document precision filters for NER findings (M10, R-30; observed false positives from rag-quality.md §3.2)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from leave_assistant.application.privacy.filters import (
    filter_document_findings,
    keep_in_document,
    organisational_terms,
)
from leave_assistant.domain.privacy.types import EntityType, Finding

TERMS = organisational_terms(["Οικονομικές Υπηρεσίες", "Ανθρώπινο Δυναμικό", "Engineering"])


def ner(entity: EntityType, text: str, source: str = "presidio") -> Finding:
    return Finding(entity, 0, len(text), 0.85, source, text)


@pytest.mark.parametrize(
    ("entity", "text"),
    [
        (EntityType.PERSON, "Who approves"),  # FAQ heading "## Who approves my request?"
        (EntityType.ADDRESS, "submits it only after you confirm"),
        (EntityType.ADDRESS, "Τμήματος Ανθρώπινου Δυναμικού"),  # the HR department, genitive
        (EntityType.ADDRESS, "Τμήμα Ανθρώπινου Δυναμικού"),
        (EntityType.PERSON, "Human Resources"),
        (EntityType.PERSON, "Οικονομικές Υπηρεσίες"),  # a department name of the tenant
        (EntityType.PERSON, "Your manager"),
        (EntityType.ADDRESS, "Αθήνα"),  # a city alone is not personal data in a policy text
        (EntityType.PERSON, "Έως"),  # a capitalised sentence start (seed FAQ)
        (EntityType.PERSON, "St Brigid's"),  # a holiday name (Brightwater holidays)
        (EntityType.PERSON, "Παπαδόπουλος"),  # a lone surname: employees are the known-entity layer's job
    ],
)
def test_observed_false_positives_are_dropped(entity: EntityType, text: str) -> None:
    assert not keep_in_document(ner(entity, text), TERMS)


@pytest.mark.parametrize(
    ("entity", "text"),
    [
        (EntityType.PERSON, "Κατερίνα Λαμπράκη"),
        (EntityType.PERSON, "John O'Neill"),
        (EntityType.ADDRESS, "Λεωφόρος Συγγρού 112"),
        (EntityType.ADDRESS, "12 Harbour Street"),
    ],
)
def test_real_names_and_addresses_are_kept(entity: EntityType, text: str) -> None:
    assert keep_in_document(ner(entity, text), TERMS)


def test_known_entities_and_pattern_types_are_never_filtered() -> None:
    known = Finding(EntityType.PERSON, 0, 3, 1.0, "known_entity", "who", subject_employee_id=uuid4())
    email = ner(EntityType.EMAIL, "hr@aegean-logistics.example")
    phone = ner(EntityType.PHONE, "2101234567")
    dropped = ner(EntityType.PERSON, "Who approves")
    assert filter_document_findings([known, email, phone, dropped], TERMS) == [known, email, phone]
