"""Audit log reads (doc 08 §10): tenant scope for HR/Company Administrators, platform scope for Platform Admins."""

from __future__ import annotations

from leave_assistant.application.audit.entry import AuditEntry
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.pagination import Page, build_page, clamp_limit, decode_cursor
from leave_assistant.application.ports import AuditFilter, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import Principal


async def list_tenant_audit(
    principal: Principal,
    uow_factory: UnitOfWorkFactory,
    *,
    filters: AuditFilter,
    limit: int | None,
    cursor: str | None,
) -> Page[AuditEntry]:
    require(principal, Action.READ_AUDIT_TENANT)
    size = clamp_limit(limit)
    async with uow_factory(TenantContext.for_principal(principal)) as uow:
        rows = await uow.audit.list(filters=filters, limit=size + 1, after=decode_cursor(cursor))
    return build_page(rows, size, lambda e: (e.occurred_at, e.id))


async def list_platform_audit(
    principal: Principal,
    uow_factory: UnitOfWorkFactory,
    *,
    filters: AuditFilter,
    limit: int | None,
    cursor: str | None,
) -> Page[AuditEntry]:
    require(principal, Action.READ_AUDIT_PLATFORM)
    size = clamp_limit(limit)
    platform_filters = AuditFilter(
        occurred_from=filters.occurred_from,
        occurred_to=filters.occurred_to,
        action=filters.action,
        platform_only=True,
    )
    async with uow_factory(TenantContext.for_principal(principal)) as uow:
        rows = await uow.audit.list(filters=platform_filters, limit=size + 1, after=decode_cursor(cursor))
    return build_page(rows, size, lambda e: (e.occurred_at, e.id))
