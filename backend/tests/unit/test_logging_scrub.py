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
