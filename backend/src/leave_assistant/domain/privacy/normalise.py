"""Text normalisation for matching names and values: accent folding, case folding, Greek name variants."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_WS = re.compile(r"\s+")


def fold(text: str) -> str:
    """Casefold + strip combining marks (accents, dialytika) + collapse whitespace. Final sigma folds to σ."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WS.sub(" ", stripped.casefold().replace("ς", "σ")).strip()


def normalise_value(entity_type: str, value: str) -> str:
    """Vault lookup form: e-mails and names folded; numbers keep digits only; everything else folded."""
    if entity_type in ("PHONE", "AMKA", "AFM", "IBAN"):
        return (
            re.sub(r"[^0-9A-Za-z]", "", value).upper() if entity_type == "IBAN" else re.sub(r"\D", "", value)
        )
    return fold(value)


# Greek nominal endings → the forms a name takes in other cases (genitive, vocative, accusative).
# (folded spelling: final sigma is σ after `fold`)
_ENDINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ιοσ", ("ιου", "ιο", "ιε")),
    ("οσ", ("ου", "ο", "ε")),
    ("ησ", ("η",)),
    ("ασ", ("α",)),
    ("ουσ", ("ου",)),
    ("α", ("ασ",)),
    ("η", ("ησ",)),
    ("ω", ("ωσ",)),
)


def greek_variants(word: str) -> set[str]:
    """Folded inflections of a Greek first name or surname (nominative in, every case out)."""
    base = fold(word)
    variants = {base}
    for ending, others in _ENDINGS:
        if base.endswith(ending) and len(base) > len(ending) + 1:
            stem = base[: -len(ending)]
            variants.update(stem + other for other in others)
            break
    return variants


def name_variants(first_name: str, last_name: str) -> dict[str, set[str]]:
    """`{"first": {...}, "last": {...}, "initials": {...}}` — all folded."""
    return {
        "first": greek_variants(first_name),
        "last": greek_variants(last_name),
        "initials": {fold(first_name[:1]) + "." if first_name else ""} - {""},
    }


def tokens_with_offsets(text: str) -> list[tuple[int, int, str]]:
    """Word tokens (letters, digits, apostrophes, dots inside) with their character offsets."""
    pattern = r"[^\W_]\.(?![^\W_])|[^\W_]+(?:[.'’-][^\W_]+)*"  # initials keep their dot ("Μ."), words do not
    return [(m.start(), m.end(), m.group(0)) for m in re.finditer(pattern, text)]


def contains_any(text: str, needles: Iterable[str]) -> bool:
    folded = fold(text)
    return any(needle and needle in folded for needle in needles)
