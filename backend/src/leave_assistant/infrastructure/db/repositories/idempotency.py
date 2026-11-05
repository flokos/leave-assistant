from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.idempotency import IdempotencyRecord
from leave_assistant.infrastructure.db.models import IdempotencyKeyRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


class SqlIdempotencyRepository:
    """`platform.idempotency_keys` has no RLS: rows are private to `user_id`, which every query filters on."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, user_id: str, route: str, key: str) -> IdempotencyRecord | None:
        stmt = select(IdempotencyKeyRow).where(
            IdempotencyKeyRow.user_id == user_id,
            IdempotencyKeyRow.route == route,
            IdempotencyKeyRow.key == key,
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return IdempotencyRecord(
            id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            route=row.route,
            key=row.key,
            request_hash=row.request_hash,
            response_status=row.response_status,
            response_body=row.response_body,
            created_at=row.created_at,
            expires_at=row.expires_at,
        )

    async def add(self, record: IdempotencyRecord) -> None:
        self._s.add(
            IdempotencyKeyRow(
                id=record.id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                route=record.route,
                key=record.key,
                request_hash=record.request_hash,
                response_status=record.response_status,
                response_body=record.response_body,
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
        )
        await flush_or_conflict(self._s)

    async def purge_expired(self, now: datetime) -> int:
        result = await self._s.execute(delete(IdempotencyKeyRow).where(IdempotencyKeyRow.expires_at <= now))
        return int(getattr(result, "rowcount", 0) or 0)
