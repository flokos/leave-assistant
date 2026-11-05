"""Company administration: leave types, policies, holidays, approval rules, simulation (doc 09 §2.8)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from leave_assistant.api.deps import CompanyAdmin, CurrentServices
from leave_assistant.api.idempotency import NO_IDEMPOTENCY, IdempotentRoute
from leave_assistant.api.schemas.catalog import (
    HolidayCreate,
    HolidayImportIn,
    HolidayOut,
    HolidayUpdate,
    LeaveTypeCreate,
    LeaveTypeUpdate,
    PolicyCreate,
    PolicyOut,
    PolicyUpdate,
    RuleCreate,
    RuleOut,
    RuleUpdate,
    SimulateIn,
)
from leave_assistant.api.schemas.leave import LeaveTypeOut, ValidationReportOut
from leave_assistant.application.leave.commands import (
    CreateHoliday,
    CreateLeaveType,
    CreatePolicy,
    CreateRule,
    SimulateRequest,
    UpdateHoliday,
    UpdateLeaveType,
    UpdatePolicy,
    UpdateRule,
)
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.domain.leave.types import StepRole

router = APIRouter(prefix="/admin", tags=["admin"], route_class=IdempotentRoute)


# -- leave types --------------------------------------------------------------------------------------------
@router.get("/leave-types", response_model=list[LeaveTypeOut])
async def list_leave_types(
    principal: CompanyAdmin, services: CurrentServices, include_inactive: bool = False
) -> list[LeaveTypeOut]:
    return [
        LeaveTypeOut.from_domain(t)
        for t in await services.catalog.list_types(principal, include_inactive=include_inactive)
    ]


@router.post("/leave-types", response_model=LeaveTypeOut, status_code=status.HTTP_201_CREATED)
async def create_leave_type(
    body: LeaveTypeCreate, principal: CompanyAdmin, services: CurrentServices
) -> LeaveTypeOut:
    created = await services.catalog.create_type(
        principal,
        CreateLeaveType(
            body.code,
            body.name_el,
            body.name_en,
            body.balance_bearing,
            body.allow_retroactive_days,
            body.requires_document_above_days,
        ),
    )
    return LeaveTypeOut.from_domain(created)


@router.patch("/leave-types/{leave_type_id}", response_model=LeaveTypeOut)
async def update_leave_type(
    leave_type_id: UUID, body: LeaveTypeUpdate, principal: CompanyAdmin, services: CurrentServices
) -> LeaveTypeOut:
    threshold = (
        body.requires_document_above_days
        if "requires_document_above_days" in body.model_fields_set
        else UNSET
    )
    updated = await services.catalog.update_type(
        principal,
        leave_type_id,
        UpdateLeaveType(
            body.name_el,
            body.name_en,
            body.balance_bearing,
            body.allow_retroactive_days,
            threshold,
            body.active,
        ),
    )
    return LeaveTypeOut.from_domain(updated)


# -- policies -----------------------------------------------------------------------------------------------
@router.get("/leave-policies", response_model=list[PolicyOut])
async def list_policies(
    principal: CompanyAdmin,
    services: CurrentServices,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[PolicyOut]:
    return [PolicyOut.from_domain(p) for p in await services.catalog.list_policies(principal, year=year)]


@router.post("/leave-policies", response_model=PolicyOut, status_code=status.HTTP_201_CREATED)
async def create_policy(body: PolicyCreate, principal: CompanyAdmin, services: CurrentServices) -> PolicyOut:
    created = await services.catalog.create_policy(
        principal,
        CreatePolicy(
            body.leave_type,
            body.year,
            body.entitlement_days,
            body.carryover_max_days,
            body.carryover_expires_on,
            body.allow_negative,
            body.min_notice_days,
            body.max_consecutive_days,
            body.prorate_on_hire,
        ),
    )
    return PolicyOut.from_domain(created)


@router.patch("/leave-policies/{policy_id}", response_model=PolicyOut)
async def update_policy(
    policy_id: UUID, body: PolicyUpdate, principal: CompanyAdmin, services: CurrentServices
) -> PolicyOut:
    fields = body.model_fields_set
    updated = await services.catalog.update_policy(
        principal,
        policy_id,
        UpdatePolicy(
            entitlement_days=body.entitlement_days,
            carryover_max_days=body.carryover_max_days,
            carryover_expires_on=body.carryover_expires_on if "carryover_expires_on" in fields else UNSET,
            allow_negative=body.allow_negative,
            min_notice_days=body.min_notice_days,
            max_consecutive_days=body.max_consecutive_days if "max_consecutive_days" in fields else UNSET,
            prorate_on_hire=body.prorate_on_hire,
        ),
    )
    return PolicyOut.from_domain(updated)


@router.delete("/leave-policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_policy(policy_id: UUID, principal: CompanyAdmin, services: CurrentServices) -> Response:
    await services.catalog.delete_policy(principal, policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- holidays -----------------------------------------------------------------------------------------------
@router.get("/holidays", response_model=list[HolidayOut])
async def list_holidays(
    principal: CompanyAdmin,
    services: CurrentServices,
    year: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> list[HolidayOut]:
    return [HolidayOut.from_domain(h) for h in await services.catalog.list_holidays(principal, year=year)]


@router.post("/holidays", response_model=HolidayOut, status_code=status.HTTP_201_CREATED)
async def create_holiday(
    body: HolidayCreate, principal: CompanyAdmin, services: CurrentServices
) -> HolidayOut:
    return HolidayOut.from_domain(
        await services.catalog.create_holiday(
            principal, CreateHoliday(body.holiday_date, body.name_el, body.name_en, body.recurring)
        )
    )


@router.post("/holidays/import", response_model=list[HolidayOut], status_code=status.HTTP_201_CREATED)
async def import_holidays(
    body: HolidayImportIn, principal: CompanyAdmin, services: CurrentServices
) -> list[HolidayOut]:
    return [
        HolidayOut.from_domain(h) for h in await services.catalog.import_holidays_csv(principal, body.csv)
    ]


@router.patch("/holidays/{holiday_id}", response_model=HolidayOut)
async def update_holiday(
    holiday_id: UUID, body: HolidayUpdate, principal: CompanyAdmin, services: CurrentServices
) -> HolidayOut:
    return HolidayOut.from_domain(
        await services.catalog.update_holiday(
            principal,
            holiday_id,
            UpdateHoliday(body.holiday_date, body.name_el, body.name_en, body.recurring),
        )
    )


@router.delete("/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_holiday(holiday_id: UUID, principal: CompanyAdmin, services: CurrentServices) -> Response:
    await services.catalog.delete_holiday(principal, holiday_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- approval rules -----------------------------------------------------------------------------------------
@router.get("/approval-rules", response_model=list[RuleOut])
async def list_rules(
    principal: CompanyAdmin, services: CurrentServices, include_inactive: bool = False
) -> list[RuleOut]:
    return [
        RuleOut.from_domain(r)
        for r in await services.catalog.list_rules(principal, include_inactive=include_inactive)
    ]


@router.post("/approval-rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(body: RuleCreate, principal: CompanyAdmin, services: CurrentServices) -> RuleOut:
    created = await services.catalog.create_rule(
        principal,
        CreateRule(
            tuple(StepRole(s) for s in body.steps),
            body.leave_type,
            body.min_working_days,
            body.max_working_days,
            body.requires_document,
            body.auto_acknowledge,
            body.priority,
        ),
    )
    return RuleOut.from_domain(created)


@router.post("/approval-rules/simulate", response_model=ValidationReportOut, openapi_extra=NO_IDEMPOTENCY)
async def simulate(
    body: SimulateIn, principal: CompanyAdmin, services: CurrentServices
) -> ValidationReportOut:
    report = await services.catalog.simulate(
        principal, SimulateRequest(body.leave_type, body.start_date, body.end_date, body.employee_id)
    )
    return ValidationReportOut.from_domain(report)


@router.patch("/approval-rules/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: UUID, body: RuleUpdate, principal: CompanyAdmin, services: CurrentServices
) -> RuleOut:
    fields = body.model_fields_set
    updated = await services.catalog.update_rule(
        principal,
        rule_id,
        UpdateRule(
            steps=tuple(StepRole(s) for s in body.steps) if body.steps is not None else None,
            leave_type_code=body.leave_type if "leave_type" in fields else UNSET,
            min_working_days=body.min_working_days,
            max_working_days=body.max_working_days if "max_working_days" in fields else UNSET,
            requires_document=body.requires_document,
            auto_acknowledge=body.auto_acknowledge,
            priority=body.priority,
            active=body.active,
        ),
    )
    return RuleOut.from_domain(updated)


@router.delete("/approval-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_rule(rule_id: UUID, principal: CompanyAdmin, services: CurrentServices) -> Response:
    await services.catalog.delete_rule(principal, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
