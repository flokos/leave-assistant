"""structlog configuration: JSON in containers, pretty console in dev shells; request_id bound per request.

The scrubbing processor (doc 08 §7.5, NFR-SEC-02) redacts sensitive keys (credentials, tokens, secrets, e-mails) at
any depth and masks e-mails, IBANs, Greek phone numbers, AMKA and AFM inside free-text values. It runs before
rendering, so nothing sensitive reaches stdout.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from leave_assistant.config import Settings

_SENSITIVE_EXACT = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "email",
        "credentials",
    }
)
_SENSITIVE_SUFFIXES = ("_token", "_secret", "_password", "_key", "_email")
_SENSITIVE_PARTS = ("password", "secret", "temporary")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# M5 (NFR-SEC-02): identifiers that must never reach stdout even inside free text. Placeholders (`PERSON_0001`)
# are safe by construction and untouched. Order matters: IBAN before the 9/11-digit patterns.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_PHONE_RE = re.compile(r"(?<![\w.])(?:\+30[ -]?)?(?:69\d|2\d{2})[ -]?\d{3}[ -]?\d{4}(?![\w.])")
_AMKA_RE = re.compile(r"(?<![\w.])\d{11}(?![\w.])")
_AFM_RE = re.compile(r"(?<![\w.])\d{9}(?![\w.])")
_TEXT_PATTERNS = (
    (_EMAIL_RE, "[email]"),
    (_IBAN_RE, "[iban]"),
    (_PHONE_RE, "[phone]"),
    (_AMKA_RE, "[amka]"),
    (_AFM_RE, "[afm]"),
)
REDACTED = "[redacted]"


def scrub_text(value: str) -> str:
    for pattern, replacement in _TEXT_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return (
        lowered in _SENSITIVE_EXACT
        or lowered.endswith(_SENSITIVE_SUFFIXES)
        or any(part in lowered for part in _SENSITIVE_PARTS)
    )


def scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and is_sensitive_key(k) else scrub_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub_value(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def scrub_event(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if isinstance(key, str) and is_sensitive_key(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = scrub_value(event_dict[key])
    return event_dict


def configure_logging(settings: Settings) -> None:
    level = logging.getLevelName(settings.log_level.upper())
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    renderer: Processor
    if settings.app_env == "dev" and sys.stdout.isatty():
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            scrub_event,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
