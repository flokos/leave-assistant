"""Deterministic pre-router (doc 04 §3), M10 rule for policy questions about permissions (R-36)."""

from __future__ import annotations

import pytest

from leave_assistant.agent.graph import CONCRETE, PRE_ROUTER
from leave_assistant.domain.agent.types import Intent


def route(text: str) -> Intent | None:
    for intent, pattern in PRE_ROUTER:
        if intent is Intent.POLICY_QUESTION and CONCRETE.search(text):
            continue
        if pattern.search(text):
            return intent
    return None


@pytest.mark.parametrize(
    "text",
    [
        "Who approves a leave request that is longer than five days?",  # M9 R02
        "Μπορώ να αλλάξω μια εγκεκριμένη άδεια πριν ξεκινήσει;",  # M9 R29
        "May I carry over unused days?",
        "Ποια είναι η διαδικασία για αναρρωτική άδεια;",
        "Επιτρέπεται να δουλέψω από το σπίτι στην άδεια;",
    ],
)
def test_permission_and_rule_questions_go_to_knowledge(text: str) -> None:
    assert route(text) is Intent.POLICY_QUESTION


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Can I cancel my request?", Intent.CANCEL),
        ("Can I have 3 days off from Monday?", Intent.REQUEST),
        ("Who is off next week?", Intent.AVAILABILITY),
        ("Ποιος λείπει την επόμενη εβδομάδα στο τμήμα Οικονομικών Υπηρεσιών;", Intent.AVAILABILITY),
        ("Θέλω 3 μέρες άδεια από 23 Δεκεμβρίου", Intent.REQUEST),
        ("Πόσες μέρες μου απομένουν;", Intent.BALANCE),
    ],
)
def test_action_rules_keep_precedence(text: str, expected: Intent) -> None:
    assert route(text) is expected


@pytest.mark.parametrize(
    "text",
    [
        "Μπορώ να πάρω 3 μέρες άδεια από Δευτέρα;",  # concrete: may be a request, the classifier decides
        "Can I take leave next week?",
        "Can I change LR-2026-000012 to Friday?",
        "Είμαι η Ελένη και θέλω να ρωτήσω κάτι",  # "είμαι" must not look like a month
    ],
)
def test_concrete_or_unrelated_wording_is_left_to_the_classifier(text: str) -> None:
    assert route(text) is None
