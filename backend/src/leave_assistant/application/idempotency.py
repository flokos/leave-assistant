"""Idempotency records (doc 09 §1): stored responses keyed by `(user_id, route, key)` for 24 hours."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    id: UUID
    tenant_id: UUID | None
    user_id: str
    route: str
    key: str
    request_hash: str
    response_status: int
    response_body: Any
    created_at: datetime
    expires_at: datetime
