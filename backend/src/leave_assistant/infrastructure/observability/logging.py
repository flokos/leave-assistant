"""structlog configuration: JSON in containers, pretty console in dev shells; request_id bound per request.

The scrubbing processor (doc 08 §7.5) redacts sensitive keys (credentials, tokens, secrets, e-mails) at any depth
and masks e-mail addresses inside free-text values. It runs before rendering, so nothing sensitive reaches stdout.
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
REDACTED = "[redacted]"


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
        return _EMAIL_RE.sub("[email]", value)
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
