"""Job administration (doc 03 §4.5): dead letters are visible to administrators and can be retried."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from leave_assistant.api.deps import AuditReader, CompanyAdmin, CurrentServices
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.jobs import JobOut
from leave_assistant.application.jobs.models import JobStatus

router = APIRouter(prefix="/admin/jobs", tags=["admin"], route_class=IdempotentRoute)


@router.get("", response_model=list[JobOut])
async def list_jobs(
    principal: AuditReader,
    services: CurrentServices,
    status_filter: Annotated[
        str | None, Query(alias="status", pattern="^(queued|running|succeeded|failed|dead)$")
    ] = None,
    type: Annotated[str | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobOut]:
    jobs = await services.jobs.list_for_tenant(
        principal, status=JobStatus(status_filter) if status_filter else None, type=type, limit=limit
    )
    return [JobOut.from_domain(j) for j in jobs]


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: UUID, principal: CompanyAdmin, services: CurrentServices) -> JobOut:
    return JobOut.from_domain(await services.jobs.retry(principal, job_id))
