"""Shared field validation for tenancy commands."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from leave_assistant.domain.errors import InvalidValue


def validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidValue(f"unknown timezone {name!r}", params={"field": "timezone"}) from exc
    return name


def validate_name(value: str, field: str, *, max_length: int = 120) -> str:
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > max_length:
        raise InvalidValue(f"{field} must be 1–{max_length} characters", params={"field": field})
    return cleaned


def validate_code(value: str, field: str = "code", *, max_length: int = 32) -> str:
    cleaned = value.strip().upper()
    if not cleaned or len(cleaned) > max_length or not all(c.isalnum() or c in "-_" for c in cleaned):
        raise InvalidValue(
            f"{field} must be 1–{max_length} letters, digits, '-' or '_'", params={"field": field}
        )
    return cleaned
