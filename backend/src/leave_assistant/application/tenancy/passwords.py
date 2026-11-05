"""Temporary passwords for provisioned users (Q-12). Generated server-side, returned once, never logged."""

from __future__ import annotations

import secrets
import string

_ALPHABET = string.ascii_letters + string.digits


def generate_temporary_password(length: int = 16) -> str:
    while True:
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if (
            any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
            and any(c.isdigit() for c in candidate)
        ):
            return candidate
