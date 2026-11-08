"""Precision filters for NER findings in company documents (M10, R-30; doc 08 §7.6).

spaCy's NER (behind Presidio) tags ordinary policy wording as PERSON or LOCATION: "Who approves" in an FAQ heading,
"…submits it only after you confirm", "Τμήμα Ανθρώπινου Δυναμικού" (the HR department) became placeholders in the
text the LLM reads (docs/evaluation/rag-quality.md §3.2). A score threshold cannot help: spaCy reports one fixed
score per entity. These filters keep a PERSON or ADDRESS finding from the statistical layer only when its span has
the shape of a name or an address, and never when it is an organisational term.

They apply to document chunks only (`profile="document"`): user messages and tool results keep the conservative
behaviour, because there a missed name is a leak and a masked word costs little. Known-entity findings (the tenant's
employees) and the pattern recognisers (e-mail, phone, AMKA, AFM, IBAN, ID) are never filtered.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from leave_assistant.domain.privacy.normalise import fold
from leave_assistant.domain.privacy.types import EntityType, Finding

STATISTICAL_SOURCE = "presidio"
_FILTERED = frozenset({EntityType.PERSON, EntityType.ADDRESS})
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")

# words that start questions, headings or sentences and are never part of a person's name (folded)
_NOT_NAMES = frozenset(
    fold(w)
    for w in (
        "who whom whose what which when where why how can may your my our their his her the a an "
        "employee employees manager managers people team staff assistant leave request requests policy "
        "annual sick unpaid parental holiday holidays day days approval approves approve hr human resources "
        "ποιος ποια ποιοι ποιο τι πότε πού πώς μπορώ ο η οι το τα του της των ένας μία μια "
        "εργαζόμενος εργαζόμενη εργαζόμενοι προϊστάμενος προϊσταμένη υπάλληλος τμήμα τμήματος άδεια άδειας "
        "αίτηση αίτησης έγκριση έγκρισης βοηθός βοηθό κανονισμός "
        "st saint άγιος αγίου αγίας αγίων"  # holiday names ("St Brigid's Day", "Αγίου Πνεύματος")
    ).split()
)

# organisational terms that are not personal data, in both languages (folded); department names are added per tenant
ORGANISATIONAL_TERMS = tuple(
    fold(t)
    for t in (
        "Human Resources",
        "People team",
        "HR department",
        "Τμήμα Ανθρώπινου Δυναμικού",
        "Ανθρώπινο Δυναμικό",
        "Ανθρώπινου Δυναμικού",
        "Τμήμα Προσωπικού",
        "Leave Assistant",
        "Βοηθός Αδειών",
        "Βοηθό Αδειών",
    )
)

# an address carries a number (street number, postcode) or a street word
_ADDRESS_HINT = re.compile(
    r"\d|\b(οδός|οδού|οδ\.|λεωφ|λεωφόρος|πλατεία|πλ\.|τ\.κ|street|st\.|road|rd\.|avenue|ave\.|lane|square|po box)",
    re.IGNORECASE,
)


def _name_shaped(span: str) -> bool:
    """At least a first name and a surname, each capitalised, none a common word. A single statistical token in a
    policy text is a capitalised sentence start far more often than a name; the tenant's employees are found by the
    known-entity layer regardless of this filter."""
    words = _WORD.findall(span)
    if len(words) < 2:
        return False
    for word in words:
        if not word[0].isupper() or fold(word) in _NOT_NAMES:
            return False
    return True


def _organisational(span: str, terms: Sequence[str]) -> bool:
    folded = fold(span)
    return any(folded == t or folded in t or t in folded for t in terms if t)


def keep_in_document(finding: Finding, terms: Sequence[str]) -> bool:
    """True when a finding from a document chunk should be pseudonymised."""
    if finding.source != STATISTICAL_SOURCE or finding.entity_type not in _FILTERED:
        return True
    if _organisational(finding.text, terms):
        return False
    if finding.entity_type is EntityType.PERSON:
        return _name_shaped(finding.text)
    return _ADDRESS_HINT.search(finding.text) is not None


def organisational_terms(department_names: Iterable[str]) -> tuple[str, ...]:
    return ORGANISATIONAL_TERMS + tuple(fold(n) for n in department_names if n.strip())


def filter_document_findings(findings: Sequence[Finding], terms: Sequence[str]) -> list[Finding]:
    return [f for f in findings if keep_in_document(f, terms)]
