"""`leave-eval-v1`: the committed dataset is complete, and labels resolve as the protocol describes."""

from __future__ import annotations

from uuid import uuid4

import pytest

from leave_assistant.evaluation.dataset import (
    ChunkText,
    Item,
    Label,
    grades_for,
    load,
    normalise,
    unresolved,
    validate,
)


def test_the_committed_dataset_is_valid_and_complete() -> None:
    items = load()
    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
    assert sum(1 for i in items if i.id.startswith("S")) == 32
    assert by_kind["probe"] + by_kind["knowledge"] >= 30 and by_kind["unanswerable"] >= 8
    assert len([i for i in items if i.ragas]) == 18
    assert all(i.reference for i in items if i.ragas)
    scenario_tests = {i.id for i in items if i.raw.get("scenario_test")}
    assert {"S01", "S05", "S09", "P01"} <= scenario_tests


def test_normalisation_handles_the_scanned_pdf() -> None:
    assert normalise("έωςτις 09:30 της πρώτης ηµέρας") == normalise(
        "έως τις 09:30 της πρώτης ημέρας"
    )  # µ → μ


def test_grades_take_the_highest_matching_label_and_respect_the_tenant() -> None:
    a, b, other = uuid4(), uuid4(), uuid4()
    chunks = [
        ChunkText(
            a, "aegean", "Κανονισμός Αδειών", "Κάθε εργαζόμενος δικαιούται είκοσι (20) εργάσιμες ημέρες"
        ),
        ChunkText(b, "aegean", "FAQ", "Twenty working days"),
        ChunkText(other, "brightwater", "Κανονισμός Αδειών", "είκοσι (20)"),
    ]
    item = Item(
        "X",
        "probe",
        "aegean",
        "u",
        "q",
        "el",
        relevant=(
            Label("Κανονισμός Αδειών", ("είκοσι (20)",), 1),
            Label("Κανονισμός Αδειών", ("ΕΊΚΟΣΙ",), 2),
        ),
    )
    assert grades_for(item, chunks) == {a: 2}
    assert unresolved([item], chunks) == []
    missing = Item("Y", "probe", "aegean", "u", "q", "el", relevant=(Label("FAQ", ("nothing",), 2),))
    assert unresolved([missing], chunks) == ["Y: FAQ ('nothing',)"]


def test_validation_rejects_inconsistent_items() -> None:
    items = load()
    with pytest.raises(ValueError, match="duplicate"):
        validate([*items, items[0]])
    broken = [i for i in items if i.id != "S32"]
    with pytest.raises(ValueError, match="32 scenarios"):
        validate(broken)
