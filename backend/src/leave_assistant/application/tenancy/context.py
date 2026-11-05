"""Transaction-scoped tenant context (ADR-0003).

The context is bound to a unit of work when it is entered; the database session issues
`SELECT set_config('app.tenant_id', …, true)` (SET LOCAL semantics) when its transaction begins. No context →
nothing is set → RLS returns zero rows (fail closed). A contextvar carries the context for the worker and
scripts; the API passes it explicitly from the Principal.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from uuid import UUID

from leave_assistant.domain.tenancy import Principal


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant_id: UUID | None
    user_id: str | None = None
    roles: tuple[str, ...] = ()
    platform_admin: bool = False

    @classmethod
    def for_principal(cls, principal: Principal) -> TenantContext:
        return cls(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            roles=tuple(sorted(role.value for role in principal.roles)),
            platform_admin=principal.is_platform_admin,
        )

    def with_tenant(self, tenant_id: UUID) -> TenantContext:
        """Explicit, audited context switch used by provisioning (doc 08 §6 point 8)."""
        return replace(self, tenant_id=tenant_id)


_current: ContextVar[TenantContext | None] = ContextVar("tenant_context", default=None)


def current_context() -> TenantContext | None:
    return _current.get()


@contextmanager
def bind_context(context: TenantContext | None) -> Iterator[None]:
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)
