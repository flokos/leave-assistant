"""Folding, Greek name variants, AMKA/AFM checksums."""

from __future__ import annotations

from leave_assistant.domain.privacy.checksums import afm_check_digit, afm_valid, amka_valid, luhn_check_digit
from leave_assistant.domain.privacy.normalise import (
    fold,
    greek_variants,
    name_variants,
    normalise_value,
    tokens_with_offsets,
)


def test_fold_strips_accents_and_case() -> None:
    assert fold("  Μαρία   Οικονόμου ") == "μαρια οικονομου"
    assert fold("Ελένη Δημητρίου") == fold("ΕΛΕΝΗ ΔΗΜΗΤΡΙΟΥ") == "ελενη δημητριου"
    assert fold("Γιώργος") == fold("ΓΙΩΡΓΟΣ") == "γιωργοσ"  # final sigma folds consistently
    assert fold("José") == "jose"


def test_greek_variants_cover_genitive_and_vocative() -> None:
    assert {"γιωργοσ", "γιωργου", "γιωργο", "γιωργε"} <= greek_variants("Γιώργος")
    assert {"παπαδακησ", "παπαδακη"} <= greek_variants("Παπαδάκης")
    assert {"μαρια", "μαριασ"} <= greek_variants("Μαρία")
    assert {"ελενη", "ελενησ"} <= greek_variants("Ελένη")
    assert {"αντωνιου"} == greek_variants("Αντωνίου") or "αντωνιου" in greek_variants("Αντωνίου")
    assert greek_variants("Kowalski") == {"kowalski"}
    variants = name_variants("Νίκος", "Παπαδάκης")
    assert "νικου" in variants["first"] and "παπαδακη" in variants["last"] and variants["initials"] == {"ν."}


def test_normalise_value_by_type() -> None:
    assert normalise_value("EMAIL", " Maria.Oikonomou@Example.COM ") == "maria.oikonomou@example.com"
    assert normalise_value("PHONE", "+30 697 123 4567") == "306971234567"
    assert normalise_value("AMKA", "01 01 80 12345") == "01018012345"
    assert normalise_value("IBAN", "gr16 0110 1250 0000 0001 2300 695") == "GR1601101250000000012300695"
    assert normalise_value("PERSON", "ΜΑΡΊΑ") == "μαρια"


def test_tokens_with_offsets() -> None:
    assert tokens_with_offsets("Η κα Οικονόμου (Μ.) ζήτησε.") == [
        (0, 1, "Η"),
        (2, 4, "κα"),
        (5, 14, "Οικονόμου"),
        (16, 18, "Μ."),
        (20, 26, "ζήτησε"),
    ]


def test_amka_and_afm_checksums() -> None:
    partial = "0101801234"
    amka = partial + luhn_check_digit(partial)
    assert amka_valid(amka) and amka_valid(amka[:4] + " " + amka[4:])
    assert not amka_valid(amka[:-1] + str((int(amka[-1]) + 1) % 10))
    assert not amka_valid("3213801234" + luhn_check_digit("3213801234"))  # invalid date 32/13
    assert not amka_valid("1234567")
    afm = "12345678" + afm_check_digit("12345678")
    assert afm_valid(afm)
    assert not afm_valid(afm[:-1] + str((int(afm[-1]) + 1) % 10))
    assert not afm_valid("000000000") and not afm_valid("12345")
