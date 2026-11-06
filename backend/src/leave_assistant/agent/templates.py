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

# --- M7: business operations (filled with fixed facts — never model text) -----------------------------------------
TEMPLATES.update(
    {
        "proposal_submit": {
            "el": "Ετοίμασα την αίτηση άδειας. Ελέγξτε τα στοιχεία στην κάρτα και επιβεβαιώστε για να υποβληθεί.",
            "en": "I have prepared the leave request. Check the details on the card and confirm to submit it.",
        },
        "proposal_modify": {
            "el": "Ετοίμασα την αλλαγή της αίτησης {request_number}. Επιβεβαιώστε στην κάρτα για να υποβληθεί.",
            "en": "I have prepared the change to request {request_number}. Confirm on the card to submit it.",
        },
        "proposal_cancel": {
            "el": "Ετοίμασα την ακύρωση της αίτησης {request_number}. Επιβεβαιώστε στην κάρτα για να γίνει.",
            "en": "I have prepared the cancellation of request {request_number}. Confirm on the card to do it.",
        },
        "proposal_approve": {
            "el": "Ετοίμασα την έγκριση της αίτησης {request_number}. Επιβεβαιώστε στην κάρτα.",
            "en": "I have prepared the approval of request {request_number}. Confirm it on the card.",
        },
        "proposal_reject": {
            "el": "Ετοίμασα την απόρριψη της αίτησης {request_number}. Επιβεβαιώστε στην κάρτα.",
            "en": "I have prepared the rejection of request {request_number}. Confirm it on the card.",
        },
        "balance_line": {
            "el": "Διαθέσιμο υπόλοιπο ({name}): {available} ημέρες.",
            "en": "Available balance ({name}): {available} days.",
        },
        "executed_submit": {
            "el": "Η αίτηση {request_number} υποβλήθηκε. Κατάσταση: {status}.",
            "en": "Request {request_number} was submitted. Status: {status}.",
        },
        "executed_modify": {
            "el": "Η αλλαγή της αίτησης υποβλήθηκε ({request_number}). Κατάσταση: {status}.",
            "en": "The change was submitted ({request_number}). Status: {status}.",
        },
        "executed_cancel": {
            "el": "Η αίτηση {request_number} ακυρώθηκε.",
            "en": "Request {request_number} was cancelled.",
        },
        "executed_approve": {
            "el": "Η έγκρισή σας καταχωρήθηκε για την αίτηση {request_number}. Κατάσταση: {status}.",
            "en": "Your approval was recorded for request {request_number}. Status: {status}.",
        },
        "executed_reject": {
            "el": "Η απόρριψη καταχωρήθηκε για την αίτηση {request_number}.",
            "en": "Your rejection was recorded for request {request_number}.",
        },
        "waiting_for": {"el": " Περιμένει έγκριση από: {steps}.", "en": " Waiting for approval by: {steps}."},
        "failed": {
            "el": "Η ενέργεια δεν εκτελέστηκε: {detail}. Δεν έγινε καμία αλλαγή.",
            "en": "The action was not carried out: {detail}. Nothing was changed.",
        },
        "rejected": {"el": "Εντάξει, δεν έγινε καμία αλλαγή.", "en": "OK, nothing was changed."},
        "expired": {
            "el": "Η πρόταση έληξε και δεν εκτελέστηκε. Ζητήστε την ξανά για να ετοιμάσω νέα.",
            "en": "The proposal expired and was not carried out. Ask again and I will prepare a new one.",
        },
        "mismatch": {
            "el": "Η πρόταση άλλαξε από τότε που εμφανίστηκε, οπότε δεν εκτελέστηκε.",
            "en": "The proposal changed since it was shown, so it was not carried out.",
        },
        "iteration_limit": {
            "el": "Δεν μπόρεσα να ολοκληρώσω το αίτημα. Δοκιμάστε να το διατυπώσετε πιο απλά ή δείτε τη σελίδα «Οι άδειές μου».",
            "en": "I could not complete that. Try phrasing it more simply, or use the “My leave” page.",
        },
    }
)
STATUS_NAMES: dict[str, dict[str, str]] = {
    "SUBMITTED": {"el": "υποβλήθηκε", "en": "submitted"},
    "PENDING_MANAGER": {"el": "σε αναμονή έγκρισης από τον προϊστάμενο", "en": "waiting for the manager"},
    "PENDING_HR": {"el": "σε αναμονή έγκρισης από το HR", "en": "waiting for HR"},
    "APPROVED": {"el": "εγκρίθηκε", "en": "approved"},
    "ACTIVE": {"el": "σε εξέλιξη", "en": "in progress"},
    "COMPLETED": {"el": "ολοκληρώθηκε", "en": "completed"},
    "REJECTED": {"el": "απορρίφθηκε", "en": "rejected"},
    "CANCELLED": {"el": "ακυρώθηκε", "en": "cancelled"},
    "WITHDRAWN": {"el": "αποσύρθηκε", "en": "withdrawn"},
    "MODIFICATION_PENDING": {"el": "αλλαγή σε αναμονή", "en": "change pending"},
}
ROLE_NAMES: dict[str, dict[str, str]] = {
    "manager": {"el": "προϊστάμενος", "en": "manager"},
    "hr": {"el": "HR", "en": "HR"},
}
FAILURE_NAMES: dict[str, dict[str, str]] = {
    "leave/insufficient-balance": {"el": "δεν υπάρχει αρκετό υπόλοιπο", "en": "the balance is not enough"},
    "leave/overlap": {"el": "επικαλύπτεται με άλλη αίτηση", "en": "it overlaps another request"},
    "leave/notice-period": {
        "el": "δεν τηρείται η προθεσμία ειδοποίησης",
        "en": "the notice period is not met",
    },
    "version-conflict": {
        "el": "η αίτηση άλλαξε στο μεταξύ· ζητήστε το ξανά",
        "en": "the request changed in the meantime; ask again",
    },
    "approval/step-not-pending": {
        "el": "η αίτηση δεν περιμένει πλέον την απόφασή σας",
        "en": "the request no longer waits for your decision",
    },
    "approval/segregation-of-duties": {
        "el": "δεν μπορείτε να αποφασίσετε και τα δύο βήματα της ίδιας αίτησης",
        "en": "you cannot decide both steps of the same request",
    },
    "authorization-denied": {"el": "δεν επιτρέπεται", "en": "it is not permitted"},
}


def template(key: str, language: str | None, **facts: object) -> str:
    texts = TEMPLATES[key]
    text = texts.get(language or "en", texts["en"])
    return text.format(**facts) if facts else text


def localised(table: dict[str, dict[str, str]], key: str, language: str | None) -> str:
    entry = table.get(key)
    if entry is None:
        return key.lower().replace("_", " ")
    return entry.get(language or "en", entry["en"])
