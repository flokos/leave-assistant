"""Enqueue `document.reindex` jobs: ACTIVE versions with a stale index generation (`make reindex`), or with `--rescan`
every indexed version that was never scanned by the M5 pipeline (`make rescan`, the upgrade back-fill)."""

from __future__ import annotations

import asyncio
import sys

from leave_assistant.application.documents.ingestion import worker_context
from leave_assistant.application.jobs.models import JOB_DOCUMENT_REINDEX
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.config import get_settings
from leave_assistant.domain.documents.types import VersionStatus
from leave_assistant.infrastructure.observability.logging import configure_logging
from leave_assistant.infrastructure.runtime import build_runtime

RESCAN_STATUSES = frozenset({VersionStatus.ACTIVE, VersionStatus.SUPERSEDED, VersionStatus.RETIRED})


async def _main(force: bool, rescan: bool) -> int:
    settings = get_settings()
    configure_logging(settings)
    runtime = build_runtime(settings, role="worker")
    services = runtime.services
    generation = services.ingestion.generation
    enqueued = 0
    try:
        async with services.uow_factory(
            TenantContext(tenant_id=None, user_id="reindex", platform_admin=True)
        ) as uow:
            tenants = await uow.tenants.list(limit=200, after=None)
        for tenant in tenants:
            async with services.uow_factory(worker_context(tenant.id)) as uow:
                statuses = RESCAN_STATUSES if rescan else frozenset({VersionStatus.ACTIVE})
                for version in await uow.document_versions.list_by_status(statuses):
                    if rescan:
                        if version.extracted_object_key is None or (version.pii_summary and not force):
                            continue
                    elif not force and version.index_generation == generation:
                        continue
                    await services.jobs.enqueue(
                        uow,
                        type=JOB_DOCUMENT_REINDEX,
                        payload={
                            "tenant_id": str(tenant.id),
                            "document_id": str(version.document_id),
                            "version_id": str(version.id),
                            "rescan": rescan,
                        },
                        tenant_id=tenant.id,
                        dedupe_key=f"{JOB_DOCUMENT_REINDEX}:{version.id}",
                    )
                    enqueued += 1
                await uow.commit()
        sys.stdout.write(f"generation {generation}: {enqueued} reindex job(s) enqueued\n")
        return 0
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(force="--force" in sys.argv[1:], rescan="--rescan" in sys.argv[1:])))
