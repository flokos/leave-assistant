"""Ports (interfaces) implemented by infrastructure adapters. Filled in milestone by milestone (doc 03 §4.3)."""

from __future__ import annotations

from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> object: ...
