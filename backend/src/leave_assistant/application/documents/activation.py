"""Activating a version: it becomes the document's retrievable version and supersedes the previous one (doc 05 §8)."""

from __future__ import annotations

from datetime import datetime

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.ports import UnitOfWork
from leave_assistant.domain.documents.lifecycle import VersionEvent, can, transition
from leave_assistant.domain.documents.types import Document, DocumentVersion
from leave_assistant.domain.tenancy import Principal


async def activate_version(
    uow: UnitOfWork,
    document: Document,
    version: DocumentVersion,
    *,
    now: datetime,
    audit: AuditService,
    principal: Principal | None,
    event: VersionEvent,
) -> None:
    version.status = transition(version.status, event)
    version.retrievable = True
    version.updated_at = now
    await uow.document_versions.update(version)
    await uow.chunks.set_retrievable(version.id, True)
    previous_id = document.active_version_id
    if previous_id is not None and previous_id != version.id:
        previous = await uow.document_versions.get(previous_id)
        if previous is not None:
            if can(previous.status, VersionEvent.SUPERSEDE):
                previous.status = transition(previous.status, VersionEvent.SUPERSEDE)
            previous.retrievable = False
            previous.updated_at = now
            await uow.document_versions.update(previous)
            await uow.chunks.set_retrievable(previous.id, False)
    document.active_version_id = version.id
    document.updated_at = now
    await uow.documents.update(document, expected_version=document.version)
    await audit.record(
        uow,
        principal,
        "document.version.activated",
        resource_type="document_version",
        resource_id=version.id,
        details={
            "document_id": str(document.id),
            "version_no": version.version_no,
            "superseded_version_id": str(previous_id) if previous_id and previous_id != version.id else None,
        },
    )
