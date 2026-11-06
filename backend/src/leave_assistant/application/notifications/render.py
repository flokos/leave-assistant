"""E-mail rendering of notifications (M8 decision 9; doc 06 W4).

One template per type and locale (the recipient's), mirroring the in-app texts of the SPA. Plain string templates
plus `html.escape` — no template engine. Parameters are only the identifiers and dates the notification already
carries (never reasons, comments or document content); codes are turned into labels of the recipient's language.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "annual": "Annual leave",
        "sick": "Sick leave",
        "unpaid": "Unpaid leave",
        "manager": "Manager",
        "hr": "HR",
        "company_shared": "company",
        "personal": "personal",
        "create": "create",
        "update": "update",
        "delete": "delete",
        "SUBMITTED": "submitted",
        "PENDING_MANAGER": "pending manager",
        "PENDING_HR": "pending HR",
        "APPROVED": "approved",
        "PENDING_REVIEW": "pending review",
        "QUARANTINED": "quarantined",
        "ACTIVE": "active",
        "FAILED": "failed",
    },
    "el": {
        "annual": "Κανονική άδεια",
        "sick": "Αναρρωτική άδεια",
        "unpaid": "Άδεια άνευ αποδοχών",
        "manager": "Προϊστάμενος",
        "hr": "HR",
        "company_shared": "εταιρικό",
        "personal": "προσωπικό",
        "create": "δημιουργία",
        "update": "ενημέρωση",
        "delete": "διαγραφή",
        "SUBMITTED": "υποβλήθηκε",
        "PENDING_MANAGER": "εκκρεμεί προϊστάμενος",
        "PENDING_HR": "εκκρεμεί HR",
        "APPROVED": "εγκρίθηκε",
        "PENDING_REVIEW": "εκκρεμεί έλεγχος",
        "QUARANTINED": "σε καραντίνα",
        "ACTIVE": "ενεργό",
        "FAILED": "απέτυχε",
    },
}

# type → (link path, {locale: (subject, body)})
TEMPLATES: dict[str, tuple[str, dict[str, tuple[str, str]]]] = {
    "request.submitted": (
        "/leave",
        {
            "en": (
                "Request {request_number} submitted",
                "Your request {request_number} was submitted ({status}).",
            ),
            "el": (
                "Υποβολή αίτησης {request_number}",
                "Η αίτησή σας {request_number} υποβλήθηκε ({status}).",
            ),
        },
    ),
    "approval.pending": (
        "/approvals",
        {
            "en": (
                "Request {request_number} awaits your decision",
                "Request {request_number} ({leave_type}, {start_date} – {end_date}, {working_days} days) awaits "
                "your decision (step {step_no}, {approver_role}).",
            ),
            "el": (
                "Η αίτηση {request_number} περιμένει την απόφασή σας",
                "Η αίτηση {request_number} ({leave_type}, {start_date} – {end_date}, {working_days} ημέρες) "
                "περιμένει την απόφασή σας (βήμα {step_no}, {approver_role}).",
            ),
        },
    ),
    "request.approved": (
        "/leave",
        {
            "en": ("Request {request_number} approved", "Request {request_number} was approved."),
            "el": ("Έγκριση αίτησης {request_number}", "Η αίτηση {request_number} εγκρίθηκε."),
        },
    ),
    "request.rejected": (
        "/leave",
        {
            "en": ("Request {request_number} rejected", "Request {request_number} was rejected."),
            "el": ("Απόρριψη αίτησης {request_number}", "Η αίτηση {request_number} απορρίφθηκε."),
        },
    ),
    "request.cancelled": (
        "/leave",
        {
            "en": ("Request {request_number} cancelled", "Request {request_number} was cancelled."),
            "el": ("Ακύρωση αίτησης {request_number}", "Η αίτηση {request_number} ακυρώθηκε."),
        },
    ),
    "sick.auto_acknowledged": (
        "/team/requests",
        {
            "en": (
                "Sick leave {request_number} acknowledged",
                "Sick leave {request_number} ({start_date} – {end_date}) was acknowledged automatically.",
            ),
            "el": (
                "Αναγνώριση αναρρωτικής {request_number}",
                "Η αναρρωτική άδεια {request_number} ({start_date} – {end_date}) αναγνωρίστηκε αυτόματα.",
            ),
        },
    ),
    "document.processed": (
        "/documents",
        {
            "en": ("Document processed", "Document “{title}” v{version_no} was processed ({status})."),
            "el": ("Επεξεργασία εγγράφου", "Το έγγραφο «{title}» v{version_no} επεξεργάστηκε ({status})."),
        },
    ),
    "document.failed": (
        "/documents",
        {
            "en": ("Document processing failed", "Document “{title}” v{version_no} could not be processed."),
            "el": (
                "Αποτυχία επεξεργασίας εγγράφου",
                "Το έγγραφο «{title}» v{version_no} δεν μπόρεσε να επεξεργαστεί.",
            ),
        },
    ),
    "document.review_required": (
        "/admin/review-queue",
        {
            "en": ("Document needs review", "Document “{title}” v{version_no} needs review ({status})."),
            "el": ("Έγγραφο προς έλεγχο", "Το έγγραφο «{title}» v{version_no} χρειάζεται έλεγχο ({status})."),
        },
    ),
    "document.processing_stalled": (
        "/documents",
        {
            "en": (
                "Document processing is taking long",
                "Document “{title}” v{version_no} has been processing for more than {minutes} minutes.",
            ),
            "el": (
                "Η επεξεργασία εγγράφου καθυστερεί",
                "Το έγγραφο «{title}» v{version_no} είναι σε επεξεργασία περισσότερο από {minutes} λεπτά.",
            ),
        },
    ),
    "document.review_reminder": (
        "/admin/review-queue",
        {
            "en": (
                "Documents waiting for review",
                "{count} document(s) have been waiting for review for over 48 hours.",
            ),
            "el": ("Έγγραφα σε αναμονή ελέγχου", "{count} έγγραφο(-α) περιμένουν έλεγχο πάνω από 48 ώρες."),
        },
    ),
    "supporting.uploaded": (
        "/leave",
        {
            "en": (
                "Supporting document attached",
                "A supporting document ({filename}) was attached to {request_number}.",
            ),
            "el": (
                "Επισύναψη δικαιολογητικού",
                "Επισυνάφθηκε δικαιολογητικό ({filename}) στην αίτηση {request_number}.",
            ),
        },
    ),
    "retention.items_found": (
        "/admin/retention",
        {
            "en": ("Retention review", "{created} item(s) are eligible for retention review."),
            "el": ("Εξέταση διατήρησης", "{created} στοιχείο(-α) είναι επιλέξιμα για εξέταση διατήρησης."),
        },
    ),
    "retention.review_reminder": (
        "/admin/retention",
        {
            "en": ("Retention items waiting", "{count} retention item(s) are waiting for your decision."),
            "el": (
                "Στοιχεία διατήρησης σε αναμονή",
                "{count} στοιχείο(-α) διατήρησης περιμένουν την απόφασή σας.",
            ),
        },
    ),
    "calendar.connection_expired": (
        "/calendar",
        {
            "en": (
                "Calendar connection expired",
                "Your {kind} calendar connection expired. Re-authorise it to resume synchronisation.",
            ),
            "el": (
                "Η σύνδεση ημερολογίου έληξε",
                "Η σύνδεση του {kind} ημερολογίου έληξε. Εξουσιοδοτήστε ξανά για να συνεχιστεί ο συγχρονισμός.",
            ),
        },
    ),
    "calendar.sync_failed": (
        "/calendar",
        {
            "en": (
                "Calendar synchronisation failed",
                "Calendar synchronisation ({operation}) for request {request_number} failed. You can retry it on the "
                "calendar page.",
            ),
            "el": (
                "Αποτυχία συγχρονισμού ημερολογίου",
                "Ο συγχρονισμός ημερολογίου ({operation}) για την αίτηση {request_number} απέτυχε. Μπορείτε να τον "
                "επαναλάβετε από τη σελίδα ημερολογίου.",
            ),
        },
    ),
}
FOOTER = {
    "en": ("Open the Leave Assistant", "This message was sent automatically; please do not reply."),
    "el": ("Άνοιγμα του Leave Assistant", "Το μήνυμα στάλθηκε αυτόματα· παρακαλούμε μην απαντήσετε."),
}


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    subject: str
    text: str
    html: str
    link: str


class _Params(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "—"


def _value(value: Any, locale: str) -> str:
    text = "" if value is None else str(value)
    return LABELS[locale].get(text, text)


def render(type: str, params: dict[str, Any], locale: str, *, base_url: str) -> RenderedEmail | None:
    """`None` for a type without an e-mail template (it stays in-app only)."""
    entry = TEMPLATES.get(type)
    if entry is None:
        return None
    path, texts = entry
    locale = locale if locale in texts else "en"
    subject_t, body_t = texts[locale]
    values = _Params({k: _value(v, locale) for k, v in params.items()})
    subject = subject_t.format_map(values)
    body = body_t.format_map(values)
    link = f"{base_url.rstrip('/')}{path}"
    open_label, footer = FOOTER[locale]
    text = f"{body}\n\n{open_label}: {link}\n\n— {footer}\n"
    page = (
        f'<!doctype html><html lang="{locale}"><body style="font-family: sans-serif">'
        f"<p>{html.escape(body)}</p>"
        f'<p><a href="{html.escape(link, quote=True)}">{html.escape(open_label)}</a></p>'
        f'<p style="color:#888;font-size:12px">{html.escape(footer)}</p></body></html>'
    )
    return RenderedEmail(subject=subject, text=text, html=page, link=link)
