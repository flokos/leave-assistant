"""E-mail rendering (M8 decision 9): every type the backend emits has EL and EN templates; HTML is escaped."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from leave_assistant.application.automation.service import REMINDER_TYPES
from leave_assistant.application.notifications.render import TEMPLATES, render

SRC = Path(__file__).resolve().parents[2] / "src" / "leave_assistant"


def emitted_types() -> set[str]:
    """Types passed to `notify(...)` anywhere in the backend (the third positional argument)."""
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\.notify\((.*?)\)\n", text, re.S):
            found.update(re.findall(r'"([a-z_]+\.[a-z_]+)"', match.group(1))[:1])
        found.update(re.findall(r'notify_step\(.*?"(approval\.pending)"', text, re.S))
    return found


def test_every_emitted_type_has_both_locales() -> None:
    emitted = emitted_types() | set(REMINDER_TYPES) | {"approval.pending"}
    assert {"request.approved", "calendar.sync_failed", "document.processed"} <= emitted
    missing = sorted(t for t in emitted if t not in TEMPLATES)
    assert missing == []
    for type_, (path, texts) in TEMPLATES.items():
        assert set(texts) == {"en", "el"} and path.startswith("/"), type_


def test_labels_follow_the_recipient_locale() -> None:
    params = {
        "request_number": "LR-2026-000007",
        "leave_type": "annual",
        "start_date": "2026-12-14",
        "end_date": "2026-12-18",
        "working_days": "5",
        "step_no": 1,
        "approver_role": "manager",
    }
    en = render("approval.pending", params, "en", base_url="http://localhost:8080/")
    el = render("approval.pending", params, "el", base_url="http://localhost:8080")
    assert en and el
    assert (
        "Annual leave" in en.text
        and "step 1, Manager" in en.text
        and en.link == "http://localhost:8080/approvals"
    )
    assert "Κανονική άδεια" in el.text and "Προϊστάμενος" in el.text and "LR-2026-000007" in el.subject
    assert render("approval.pending", params, "de", base_url="x") is not None  # unknown locale → English


def test_html_is_escaped_and_missing_params_do_not_crash() -> None:
    email = render(
        "document.processed", {"title": "<script>x</script> & co", "version_no": 2}, "en", base_url="h"
    )
    assert email is not None
    assert "<script>" not in email.html and "&lt;script&gt;" in email.html and "&amp; co" in email.html
    assert "({status})" not in email.text and "—" in email.text  # absent parameter → dash
    assert render("unknown.type", {}, "en", base_url="h") is None


def test_failure_mail_omits_the_error_text() -> None:
    email = render(
        "document.failed",
        {"title": "Scan", "version_no": 1, "error": "Traceback: secret"},
        "en",
        base_url="h",
    )
    assert email is not None and "Traceback" not in email.text


@pytest.mark.parametrize("type_", sorted(TEMPLATES))
def test_every_template_renders_in_both_locales(type_: str) -> None:
    for locale in ("en", "el"):
        email = render(type_, {}, locale, base_url="http://localhost:8080")
        assert email is not None and email.subject and email.text.endswith("\n")
