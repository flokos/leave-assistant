"""Stdlib implementations of the Clock and IdGenerator ports (UUIDv7 per doc 07 §1; Python 3.14 provides uuid7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Uuid7IdGenerator:
    def new_id(self) -> uuid.UUID:
        return uuid.uuid7()
