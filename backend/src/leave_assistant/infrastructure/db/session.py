"""Async SQLAlchemy engine/session factory and the transaction-scoped tenant context (ADR-0003).

`after_begin` fires once per transaction on the connection; it issues `set_config(name, value, true)` (SET LOCAL
semantics) for `app.tenant_id`, `app.user_id`, `app.roles` and `app.platform_admin` from the context frozen in
`session.info["tenant_context"]` when the unit of work was entered (falling back to the contextvar). No context →
no settings → RLS policies evaluate to false and tenant tables return no rows (fail closed).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, SessionTransaction

from leave_assistant.application.errors import ConflictError
from leave_assistant.application.tenancy.context import TenantContext, current_context
from leave_assistant.config import Settings

CONTEXT_KEY = "tenant_context"
_UNSET = object()

APPLY_CONTEXT_SQL = text(
    "SELECT set_config('app.tenant_id', :tenant_id, true), set_config('app.user_id', :user_id, true), "
    "set_config('app.roles', :roles, true), set_config('app.platform_admin', :platform_admin, true)"
)


def context_params(context: TenantContext) -> dict[str, str]:
    return {
        "tenant_id": str(context.tenant_id) if context.tenant_id else "",
        "user_id": context.user_id or "",
        "roles": ",".join(context.roles),
        "platform_admin": "true" if context.platform_admin else "false",
    }


def create_engine(
    settings: Settings, *, url: str | None = None, pool_size: int = 5, max_overflow: int = 5
) -> AsyncEngine:
    return create_async_engine(
        url or settings.database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=1800,
        pool_reset_on_return="rollback",
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    context: Any = session.info.get(CONTEXT_KEY, _UNSET)
    if context is _UNSET:
        context = current_context()
    if context is None:
        return
    connection.execute(APPLY_CONTEXT_SQL, context_params(context))


async def ping(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def flush_or_conflict(session: AsyncSession) -> None:
    """Flush pending rows; a unique/foreign-key violation becomes a 409 (the transaction is rolled back)."""
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("the change conflicts with existing data") from exc
