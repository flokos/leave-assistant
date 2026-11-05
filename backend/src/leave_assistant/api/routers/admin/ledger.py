"""Ledger administration (doc 09 §2.8): HR adjustments and tenant balances."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from leave_assistant.api.deps import AuditReader, CurrentPrincipal, CurrentServices, require_tenant_roles
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.catalog import AdjustmentIn, TenantBalanceOut
from leave_assistant.api.schemas.leave import LedgerEntryOut
from leave_assistant.application.leave.commands import Adjustment
from leave_assistant.domain.tenancy import Principal, Role

router = APIRouter(prefix="/admin", tags=["admin"], route_class=IdempotentRoute)
HrAdmin = Annotated[Principal, Depends(require_tenant_roles(Role.HR_ADMIN))]


@router.post("/ledger/adjustments", response_model=LedgerEntryOut, status_code=status.HTTP_201_CREATED)
async def post_adjustment(
    body: AdjustmentIn, principal: HrAdmin, services: CurrentServices
) -> LedgerEntryOut:
    entry = await services.ledger.adjust(
        principal,
        Adjustment(
            body.employee_id,
            body.leave_type,
            body.quantity_days,
            body.reason,
            body.period_year,
            body.effective_date,
        ),
    )
    return LedgerEntryOut.from_domain(entry)


@router.get("/balances", response_model=list[TenantBalanceOut])
async def tenant_balances(
    principal: AuditReader,
    services: CurrentServices,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[TenantBalanceOut]:
    year = year or services.clock.now().year
    return [TenantBalanceOut.from_row(r) for r in await services.ledger.tenant_balances(principal, year=year)]


__all__ = ["CurrentPrincipal", "router"]
