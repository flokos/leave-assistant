"""Team endpoints (doc 09 §2.4): department scope for managers, tenant scope for HR, absence only for admins."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from leave_assistant.api.deps import CurrentPrincipal, CurrentServices
from leave_assistant.api.schemas.common import PageOut
from leave_assistant.api.schemas.team import AbsenceOut, TeamEmployeeOut, TeamRequestOut
from leave_assistant.domain.leave.state_machine import RequestStatus

router = APIRouter(prefix="/team", tags=["team"])


@router.get("/employees", response_model=list[TeamEmployeeOut])
async def employees(principal: CurrentPrincipal, services: CurrentServices) -> list[TeamEmployeeOut]:
    return [TeamEmployeeOut.from_domain(e) for e in await services.team.employees(principal)]


@router.get("/availability", response_model=list[AbsenceOut])
async def availability(
    principal: CurrentPrincipal,
    services: CurrentServices,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> list[AbsenceOut]:
    return [
        AbsenceOut.from_view(r)
        for r in await services.team.availability(principal, start=date_from, end=date_to)
    ]


@router.get("/requests", response_model=PageOut[TeamRequestOut])
async def requests(
    principal: CurrentPrincipal,
    services: CurrentServices,
    status_filter: Annotated[list[RequestStatus] | None, Query(alias="status")] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: str | None = None,
) -> PageOut[TeamRequestOut]:
    page = await services.team.requests(
        principal, statuses=frozenset(status_filter) if status_filter else None, limit=limit, cursor=cursor
    )
    return PageOut(items=[TeamRequestOut.from_view(r) for r in page.items], next_cursor=page.next_cursor)
