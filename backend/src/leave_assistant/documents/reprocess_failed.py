"""Re-queue every FAILED document version of every tenant (`make retry-failed`, M10).

The operator counterpart of `POST /documents/{id}/versions/{n}/reprocess`: the version goes FAILED → UPLOADED with
its error cleared and a fresh `document.process` job, then `make ingest` drains it. Found by the M10 clean-clone test:
documents seeded before the models were downloaded failed all their attempts and had no bulk retry.
"""

from __future__ import annotations

import asyncio
import sys

from leave_assistant.application.documents.ingestion import worker_context
from leave_assistant.application.jobs.models import JOB_DOCUMENT_PROCESS
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.config import get_settings
from leave_assistant.domain.documents.lifecycle import VersionEvent, transition
from leave_assistant.domain.documents.types import VersionStatus
from leave_assistant.infrastructure.observability.logging import configure_logging
from leave_assistant.infrastructure.runtime import build_runtime


async def _main() -> int:
    settings = get_settings()
    configure_logging(settings)
    runtime = build_runtime(settings, role="worker")
    services = runtime.services
    requeued = 0
    try:
        async with services.uow_factory(
            TenantContext(tenant_id=None, user_id="retry-failed", platform_admin=True)
        ) as uow:
            tenants = await uow.tenants.list(limit=200, after=None)
        for tenant in tenants:
            async with services.uow_factory(worker_context(tenant.id)) as uow:
                for version in await uow.document_versions.list_by_status(frozenset({VersionStatus.FAILED})):
                    now = services.clock.now()
                    version.status = transition(version.status, VersionEvent.REPROCESS)
                    version.processing_error = None
                    version.updated_at = now
                    await uow.document_versions.update(version)
                    await services.jobs.enqueue(
                        uow,
                        type=JOB_DOCUMENT_PROCESS,
                        payload={
                            "tenant_id": str(tenant.id),
                            "document_id": str(version.document_id),
                            "version_id": str(version.id),
                        },
                        tenant_id=tenant.id,
                        dedupe_key=f"{JOB_DOCUMENT_PROCESS}:{version.id}",
                    )
                    requeued += 1
                await uow.commit()
        sys.stdout.write(f"{requeued} failed version(s) re-queued; run `make ingest` to process them\n")
        return 0
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
