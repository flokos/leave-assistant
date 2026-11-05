"""Templated, LLM-free replies (doc 04 §3): insufficient evidence, clarification, scope, blocked input, business
intents until the M7 tools exist. Pseudonymisation is irrelevant here — the texts carry no personal data."""

from __future__ import annotations

TEMPLATES: dict[str, dict[str, str]] = {
    "insufficient_evidence": {
        "el": (
            "Δεν βρήκα αρκετά στοιχεία στα έγγραφα της εταιρείας για να απαντήσω με βεβαιότητα. "
            "Για αυτό το θέμα απευθυνθείτε στο τμήμα Ανθρώπινου Δυναμικού."
        ),
        "en": (
            "I could not find enough information in the company's documents to answer this reliably. "
            "Please contact Human Resources about it."
        ),
    },
    "clarify": {
        "el": "Δεν είμαι σίγουρος τι ακριβώς ρωτάτε. Μπορείτε να το διατυπώσετε πιο συγκεκριμένα;",
        "en": "I am not sure what you are asking. Could you phrase it more specifically?",
    },
    "scope": {
        "el": (
            "Μπορώ να βοηθήσω με ερωτήσεις για τις άδειες της εταιρείας: δικαιώματα, μεταφορά υπολοίπου, αργίες, "
            "αναρρωτική άδεια και διαδικασίες."
        ),
        "en": (
            "I can help with questions about the company's leave: entitlements, carry-over, holidays, sick leave "
            "and procedures."
        ),
    },
    "smalltalk": {
        "el": "Γεια σας! Ρωτήστε με οτιδήποτε για τις άδειες της εταιρείας.",
        "en": "Hello! Ask me anything about the company's leave rules.",
    },
    "blocked": {
        "el": "Δεν μπορώ να βοηθήσω με αυτό το αίτημα.",
        "en": "I cannot help with that request.",
    },
    "business": {
        "el": (
            "Αυτή την ενέργεια δεν μπορώ να την κάνω ακόμη από τη συνομιλία. Θα τη βρείτε στη σελίδα «Οι άδειές μου» "
            "(υπόλοιπο, ιστορικό, νέα αίτηση) ή στις «Εγκρίσεις»."
        ),
        "en": (
            "I cannot do this from the chat yet. You will find it on the “My leave” page (balance, history, new "
            "request) or under “Approvals”."
        ),
    },
}


def template(name: str, language: str | None) -> str:
    texts = TEMPLATES[name]
    return texts.get(language or "en", texts["en"])
