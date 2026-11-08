"""Department lookup by code or name (M10, doc 11 S19): the assistant passes what the user wrote, e.g. "τμήμα
Οικονομικών Υπηρεσιών" for the department "Οικονομικές Υπηρεσίες" (code FIN).

Resolution order: the code (case-insensitive) → the exact name (case, accents and whitespace folded) → every word of
the department's name, reduced to its stem, appears among the stems of the text (Greek case endings and English
plurals differ between the question and the stored name) → one distinctive word stem in common ("Οικονομικών" for
"Οικονομικές Υπηρεσίες"; generic words such as "τμήμα" or "department" do not count). Each step must give a unique
match; otherwise nothing is returned and the caller reports "not found" rather than guessing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from leave_assistant.domain.privacy.normalise import fold
from leave_assistant.domain.tenancy.entities import Department

# folded endings (fold() maps final ς to σ and strips accents), longest first
_ENDINGS = ("ουσ", "ων", "ου", "ησ", "εσ", "οσ", "ασ", "ισ", "α", "η", "ο", "ι", "ε", "σ", "υ", "s")
_WORD = re.compile(r"\w+")
_MIN_STEM = 3
_GENERIC = frozenset({"τμημ", "τμηματ", "υπηρεσι", "department", "dept", "team", "the", "του", "τησ", "των"})


def stem(word: str) -> str:
    folded = fold(word)
    for ending in _ENDINGS:
        if folded.endswith(ending) and len(folded) - len(ending) >= _MIN_STEM:
            return folded[: -len(ending)]
    return folded


def stems(text: str) -> set[str]:
    return {stem(w) for w in _WORD.findall(text)}


def resolve_department(text: str, departments: Sequence[Department]) -> Department | None:
    wanted = text.strip()
    if not wanted:
        return None
    by_code = [d for d in departments if d.code.upper() == wanted.upper()]
    if len(by_code) == 1:
        return by_code[0]
    folded = fold(wanted)
    exact = [d for d in departments if fold(d.name) == folded]
    if len(exact) == 1:
        return exact[0]
    words = stems(wanted)
    by_stems = [d for d in departments if (name := stems(d.name)) and name <= words]
    if by_stems:
        return by_stems[0] if len(by_stems) == 1 else None
    distinctive = {w for w in words if len(w) >= 4 and w not in _GENERIC}
    partial = [d for d in departments if stems(d.name) & distinctive]
    return partial[0] if len(partial) == 1 else None
