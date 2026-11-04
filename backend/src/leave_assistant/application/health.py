"""Readiness model shared by the API layer and the infrastructure checks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


ReadinessCheck = Callable[[], Awaitable[CheckResult]]


@dataclass(frozen=True)
class Readiness:
    ok: bool
    checks: tuple[CheckResult, ...]


async def run_readiness(checks: Sequence[ReadinessCheck]) -> Readiness:
    results = await asyncio.gather(*(check() for check in checks))
    return Readiness(ok=all(r.ok for r in results), checks=tuple(results))
