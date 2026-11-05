"""Child-process entry: `python -m leave_assistant.worker.run_job <job_id>` runs one claimed job in isolation.

The parent claimed the job and renews its heartbeat; this process loads the models, runs the handler and records
the outcome itself (exit code 0 on success, 1 on failure). Memory is returned to the OS when it exits.
"""

from __future__ import annotations

import asyncio
import sys
from uuid import UUID

from leave_assistant.application.jobs.models import JobStatus
from leave_assistant.config import get_settings
from leave_assistant.infrastructure.observability.logging import configure_logging, get_logger
from leave_assistant.infrastructure.runtime import build_runtime
from leave_assistant.worker.handlers import build_registry
from leave_assistant.worker.runner import JobRunner

log = get_logger("worker.child")


async def _main(job_id: UUID) -> int:
    settings = get_settings()
    configure_logging(settings)
    runtime = build_runtime(settings, role="worker")
    try:
        job = await runtime.services.jobs.get(job_id)
        if job is None or job.status is not JobStatus.RUNNING:
            log.error("job.child.not_running", job_id=str(job_id))
            return 1
        registry = build_registry(runtime.services)
        runner = JobRunner(runtime.services.jobs, registry, worker_id=f"child:{job_id}", inline=True)
        await runner.run_inline(job, registry.get(job.type))
        final = await runtime.services.jobs.get(job_id)
        return 0 if final is not None and final.status is JobStatus.SUCCEEDED else 1
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(UUID(sys.argv[1]))))
