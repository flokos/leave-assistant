"""Job handlers (doc 03 §4.5): the registry maps job types to application services."""

from __future__ import annotations

from uuid import UUID

from leave_assistant.application.calendar.models import JOB_CALENDAR_SYNC
from leave_assistant.application.container import Services
from leave_assistant.application.jobs.models import JOB_DOCUMENT_PROCESS, JOB_DOCUMENT_REINDEX, Job
from leave_assistant.application.jobs.registry import JobHandler, JobRegistry
from leave_assistant.application.retention.service import JOB_RETENTION_DELETE, JOB_RETENTION_SCAN


def _ids(job: Job) -> tuple[UUID, UUID]:
    return UUID(str(job.payload["tenant_id"])), UUID(str(job.payload["version_id"]))


def build_registry(services: Services) -> JobRegistry:
    async def process(job: Job) -> object:
        tenant_id, version_id = _ids(job)
        return await services.ingestion.process(tenant_id, version_id)

    async def reindex(job: Job) -> object:
        tenant_id, version_id = _ids(job)
        return await services.ingestion.reindex(
            tenant_id, version_id, rescore=bool(job.payload.get("rescan"))
        )

    async def on_failure(job: Job, error: str, final: bool) -> None:
        tenant_id, version_id = _ids(job)
        await services.ingestion.record_failure(tenant_id, version_id, error, final=final)

    return JobRegistry(
        [
            JobHandler(JOB_DOCUMENT_PROCESS, process, isolated=True, on_failure=on_failure),
            JobHandler(JOB_DOCUMENT_REINDEX, reindex, isolated=True, on_failure=on_failure),
            # M5: retention jobs are plain database work — inline in the worker process
            JobHandler(JOB_RETENTION_SCAN, services.retention.handle_scan_job),
            JobHandler(JOB_RETENTION_DELETE, services.retention.handle_delete_job),
            # M8: calendar sync — the service owns retries (backoff, park, dead); the queue job is a single shot
            JobHandler(JOB_CALENDAR_SYNC, services.calendar_sync.handle_job),
        ]
    )
