"""Self-service leave endpoints (doc 09 §2.3)."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices
from leave_assistant.api.idempotency import HEADER, NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.schemas.common import PageOut
from leave_assistant.api.schemas.leave import (
    BalanceOut,
    CancelRequestIn,
    LeaveRequestOut,
    LeaveRequestSummary,
    LeaveTypeOut,
    LedgerEntryOut,
    ModifyRequestIn,
    SubmitRequestIn,
    ValidationReportOut,
    WorkingDaysOut,
)
from leave_assistant.application.leave.commands import CancelRequest, ModifyRequest, SubmitRequest
from leave_assistant.application.leave.common import load_tenant, tenant_user
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.leave.working_days import classify

router = APIRouter(prefix="/leave", tags=["leave"], route_class=IdempotentRoute)


@router.get("/types", response_model=list[LeaveTypeOut])
async def list_types(principal: CurrentPrincipal, services: CurrentServices) -> list[LeaveTypeOut]:
    return [LeaveTypeOut.from_domain(t) for t in await services.catalog.list_types(principal)]


@router.get("/balances", response_model=list[BalanceOut])
async def balances(
    principal: CurrentPrincipal,
    services: CurrentServices,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[BalanceOut]:
    year = year or services.clock.now().year
    return [BalanceOut.from_view(v) for v in await services.ledger.balances(principal, year=year)]


@router.get("/ledger", response_model=list[LedgerEntryOut])
async def ledger(
    principal: CurrentPrincipal,
    services: CurrentServices,
    leave_type: Annotated[str | None, Query(alias="type", max_length=32)] = None,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[LedgerEntryOut]:
    return [
        LedgerEntryOut.from_domain(e)
        for e in await services.ledger.ledger(principal, leave_type_code=leave_type, year=year)
    ]


@router.get("/working-days", response_model=WorkingDaysOut)
async def working_days(
    principal: CurrentPrincipal, services: CurrentServices, start: date, end: date
) -> WorkingDaysOut:
    tenant_id, _ = tenant_user(principal)
    async with services.uow_factory(TenantContext.for_principal(principal)) as uow:
        tenant = await load_tenant(uow, tenant_id)
        years = tuple(range(start.year, max(start.year, end.year) + 1))
        holidays = await services.leave_requests._wf.planner.holidays_for(uow, years)  # noqa: SLF001 - read helper
    return WorkingDaysOut.from_domain(
        classify(start, end, work_week=tenant.settings.work_week, holidays=holidays)
    )


@router.post("/requests/validate", response_model=ValidationReportOut, openapi_extra=NO_IDEMPOTENCY)
async def validate_request(
    body: SubmitRequestIn, principal: CurrentPrincipal, services: CurrentServices
) -> ValidationReportOut:
    report = await services.leave_requests.validate(
        principal, SubmitRequest(body.leave_type, body.start_date, body.end_date, body.reason)
    )
    return ValidationReportOut.from_domain(report)


@router.post("/requests", response_model=LeaveRequestOut, status_code=status.HTTP_201_CREATED)
async def submit_request(
    body: SubmitRequestIn, request: Request, principal: CurrentPrincipal, services: CurrentServices
) -> LeaveRequestOut:
    detail = await services.leave_requests.submit(
        principal,
        SubmitRequest(
            body.leave_type,
            body.start_date,
            body.end_date,
            body.reason,
            idempotency_key=request.headers.get(HEADER),
        ),
    )
    return LeaveRequestOut.from_detail(detail)


@router.get("/requests", response_model=PageOut[LeaveRequestSummary])
async def list_requests(
    principal: CurrentPrincipal,
    services: CurrentServices,
    status_filter: Annotated[list[RequestStatus] | None, Query(alias="status")] = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: str | None = None,
) -> PageOut[LeaveRequestSummary]:
    page = await services.leave_requests.list(
        principal,
        statuses=frozenset(status_filter) if status_filter else None,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        cursor=cursor,
    )
    types = {t.id: t.code for t in await services.catalog.list_types(principal, include_inactive=True)}
    return PageOut(
        items=[LeaveRequestSummary.from_domain(r, types.get(r.leave_type_id)) for r in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/requests/{request_id}", response_model=LeaveRequestOut)
async def get_request(
    request_id: UUID, principal: CurrentPrincipal, services: CurrentServices
) -> LeaveRequestOut:
    return LeaveRequestOut.from_detail(await services.leave_requests.get(principal, request_id))


@router.post("/requests/{request_id}/modify", response_model=LeaveRequestOut)
async def modify_request(
    request_id: UUID, body: ModifyRequestIn, principal: CurrentPrincipal, services: CurrentServices
) -> LeaveRequestOut:
    reason = body.reason if "reason" in body.model_fields_set else UNSET
    detail = await services.leave_requests.modify(
        principal, request_id, ModifyRequest(body.start_date, body.end_date, body.version, reason)
    )
    return LeaveRequestOut.from_detail(detail)


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestOut)
async def cancel_request(
    request_id: UUID, body: CancelRequestIn, principal: CurrentPrincipal, services: CurrentServices
) -> LeaveRequestOut:
    detail = await services.leave_requests.cancel(
        principal, request_id, CancelRequest(body.version, body.comment)
    )
    return LeaveRequestOut.from_detail(detail)
