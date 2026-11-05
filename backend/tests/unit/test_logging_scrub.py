"""NFR-SEC-02: no secret, token or e-mail reaches the logs."""

from __future__ import annotations

from leave_assistant.infrastructure.observability.logging import REDACTED, scrub_event


def test_sensitive_keys_redacted_at_any_depth() -> None:
    event = {
        "event": "x",
        "authorization": "Bearer abc",
        "nested": {
            "client_secret": "s",
            "access_token": "t",
            "temporary_password": "p",
            "KeycloakAdminPassword": "q",
        },
        "items": [{"password": "x"}, "plain"],
        "user_email": "a@b.example",
    }
    out = scrub_event(None, "info", event)  # type: ignore[arg-type]
    assert out["authorization"] == REDACTED
    assert out["nested"] == {
        "client_secret": REDACTED,
        "access_token": REDACTED,
        "temporary_password": REDACTED,
        "KeycloakAdminPassword": REDACTED,
    }
    assert out["items"] == [{"password": REDACTED}, "plain"]
    assert out["user_email"] == REDACTED


def test_emails_masked_inside_text() -> None:
    out = scrub_event(None, "info", {"event": "created maria.oikonomou@aegean-logistics.example ok", "n": 3})  # type: ignore[arg-type]
    assert out["event"] == "created [email] ok"
    assert out["n"] == 3


def test_identifiers_masked_inside_text_and_placeholders_untouched() -> None:
    event = {
        "event": (
            "contact 6971234567 or +30 210 123 4567, AMKA 01018012345, AFM 123456789, "
            "IBAN GR16 0110 1250 0000 0001 2300 695, see PERSON_0001 and EMAIL_0002"
        )
    }
    out = scrub_event(None, "info", event)  # type: ignore[arg-type]
    text = out["event"]
    assert "6971234567" not in text and "[phone]" in text
    assert "01018012345" not in text and "[amka]" in text
    assert "123456789" not in text and "[afm]" in text
    assert "GR16" not in text and "[iban]" in text
    assert "PERSON_0001" in text and "EMAIL_0002" in text


def test_scrub_text_keeps_ordinary_numbers() -> None:
    from leave_assistant.infrastructure.observability.logging import scrub_text

    assert (
        scrub_text("request 2026-11-05 took 125 ms, version 3") == "request 2026-11-05 took 125 ms, version 3"
    )
    assert scrub_text("id 123456789012345") == "id 123456789012345"  # 15 digits: neither AMKA nor AFM
