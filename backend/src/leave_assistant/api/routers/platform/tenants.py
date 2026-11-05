"""Platform administration of tenants (doc 09 §2.9). Never returns tenant business data."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from leave_assistant.api.deps import CurrentServices, PlatformAdmin
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.common import PageOut
from leave_assistant.api.schemas.departments import DepartmentOut
from leave_assistant.api.schemas.employees import EmployeeOut
from leave_assistant.api.schemas.tenants import (
    BootstrapAdminRequest,
    BootstrapAdminResponse,
    TenantCreate,
    TenantOut,
    TenantUpdate,
)
from leave_assistant.application.tenancy.commands import BootstrapAdmin, CreateTenant, UpdateTenant
from leave_assistant.domain.tenancy import Locale, TenantStatus

router = APIRouter(prefix="/platform/tenants", tags=["platform"], route_class=IdempotentRoute)


@router.get("", response_model=PageOut[TenantOut])
async def list_tenants(
    principal: PlatformAdmin,
    services: CurrentServices,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    cursor: str | None = None,
) -> PageOut[TenantOut]:
    page = await services.provisioning.list_tenants(principal, limit=limit, cursor=cursor)
    return PageOut(items=[TenantOut.from_domain(t) for t in page.items], next_cursor=page.next_cursor)


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(body: TenantCreate, principal: PlatformAdmin, services: CurrentServices) -> TenantOut:
    tenant = await services.provisioning.create_tenant(
        principal,
        CreateTenant(
            slug=body.slug,
            name=body.name,
            default_locale=Locale(body.default_locale),
            timezone=body.timezone,
            settings=body.settings,
        ),
    )
    return TenantOut.from_domain(tenant)


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: UUID, principal: PlatformAdmin, services: CurrentServices) -> TenantOut:
    return TenantOut.from_domain(await services.provisioning.get_tenant(principal, tenant_id))


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: UUID, body: TenantUpdate, principal: PlatformAdmin, services: CurrentServices
) -> TenantOut:
    tenant = await services.provisioning.update_tenant(
        principal,
        tenant_id,
        UpdateTenant(
            name=body.name,
            default_locale=Locale(body.default_locale) if body.default_locale else None,
            timezone=body.timezone,
            settings=body.settings,
        ),
    )
    return TenantOut.from_domain(tenant)


@router.post("/{tenant_id}/suspend", response_model=TenantOut)
async def suspend_tenant(tenant_id: UUID, principal: PlatformAdmin, services: CurrentServices) -> TenantOut:
    return TenantOut.from_domain(
        await services.provisioning.set_status(principal, tenant_id, TenantStatus.SUSPENDED)
    )


@router.post("/{tenant_id}/resume", response_model=TenantOut)
async def resume_tenant(tenant_id: UUID, principal: PlatformAdmin, services: CurrentServices) -> TenantOut:
    return TenantOut.from_domain(
        await services.provisioning.set_status(principal, tenant_id, TenantStatus.ACTIVE)
    )


@router.post(
    "/{tenant_id}/bootstrap-admin",
    response_model=BootstrapAdminResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create the first Company Administrator (temporary password, forced change at first login)",
)
async def bootstrap_admin(
    tenant_id: UUID, body: BootstrapAdminRequest, principal: PlatformAdmin, services: CurrentServices
) -> BootstrapAdminResponse:
    result = await services.provisioning.bootstrap_admin(
        principal,
        tenant_id,
        BootstrapAdmin(
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            locale=Locale(body.locale),
            employee_number=body.employee_number,
            hire_date=body.hire_date,
        ),
    )
    return BootstrapAdminResponse(
        employee=EmployeeOut.from_domain(result.employee),
        department=DepartmentOut.from_domain(result.department),
        username=result.username,
        temporary_password=result.temporary_password,
    )
