"""Supporting documents, their access events and platform download tokens (M5, doc 07 §4.7)."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.ports import DownloadToken, SupportingAccessEvent, SupportingDocument
from leave_assistant.infrastructure.db.models import (
    DownloadTokenRow,
    SupportingAccessEventRow,
    SupportingDocumentRow,
)
from leave_assistant.infrastructure.db.session import flush_or_conflict

TOKEN_SWEEP_AGE = timedelta(days=1)


def _document_to_domain(r: SupportingDocumentRow) -> SupportingDocument:
    return SupportingDocument(
        id=r.id,
        tenant_id=r.tenant_id,
        request_id=r.request_id,
        employee_id=r.employee_id,
        object_key=r.object_key,
        original_filename=r.original_filename,
        mime=r.mime,
        size_bytes=int(r.size_bytes),
        sha256=r.sha256,
        encrypted_dek=bytes(r.encrypted_dek),
        status=r.status,
        uploaded_by_employee_id=r.uploaded_by_employee_id,
        created_at=r.created_at,
        retention_until=r.retention_until,
        deleted_at=r.deleted_at,
    )


class SqlSupportingDocumentRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    def _base(self) -> Select[SupportingDocumentRow]:
        return select(SupportingDocumentRow).where(SupportingDocumentRow.tenant_id == self._tenant)

    async def get(self, document_id: UUID) -> SupportingDocument | None:
        if self._tenant is None:
            return None
        row = (
            await self._s.execute(self._base().where(SupportingDocumentRow.id == document_id))
        ).scalar_one_or_none()
        return _document_to_domain(row) if row else None

    async def list_for_request(self, request_id: UUID) -> builtins.list[SupportingDocument]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(SupportingDocumentRow.request_id == request_id)
            .order_by(SupportingDocumentRow.created_at, SupportingDocumentRow.id)
        )
        return [_document_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def count_for_requests(self, request_ids: Sequence[UUID]) -> dict[UUID, int]:
        if self._tenant is None or not request_ids:
            return {}
        stmt = (
            select(SupportingDocumentRow.request_id, func.count())
            .where(
                SupportingDocumentRow.tenant_id == self._tenant,
                SupportingDocumentRow.request_id.in_(list(request_ids)),
                SupportingDocumentRow.status != "deleted",
            )
            .group_by(SupportingDocumentRow.request_id)
        )
        return {request_id: int(count) for request_id, count in (await self._s.execute(stmt)).all()}

    async def list_by_status(self, status: str) -> builtins.list[SupportingDocument]:
        if self._tenant is None:
            return []
        stmt = (
            self._base()
            .where(SupportingDocumentRow.status == status)
            .order_by(SupportingDocumentRow.created_at)
        )
        return [_document_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def add(self, document: SupportingDocument) -> None:
        self._s.add(
            SupportingDocumentRow(
                id=document.id,
                tenant_id=document.tenant_id,
                request_id=document.request_id,
                employee_id=document.employee_id,
                object_key=document.object_key,
                original_filename=document.original_filename,
                mime=document.mime,
                size_bytes=document.size_bytes,
                sha256=document.sha256,
                encrypted_dek=document.encrypted_dek,
                status=document.status,
                uploaded_by_employee_id=document.uploaded_by_employee_id,
                created_at=document.created_at,
                retention_until=document.retention_until,
                deleted_at=document.deleted_at,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, document: SupportingDocument) -> None:
        row = await self._s.get(SupportingDocumentRow, document.id)
        if row is None or row.tenant_id != self._tenant:
            return
        row.status = document.status
        row.retention_until = document.retention_until
        row.deleted_at = document.deleted_at
        await self._s.flush()


def _event_to_domain(r: SupportingAccessEventRow) -> SupportingAccessEvent:
    return SupportingAccessEvent(
        id=r.id,
        tenant_id=r.tenant_id,
        supporting_document_id=r.supporting_document_id,
        actor_employee_id=r.actor_employee_id,
        action=r.action,
        occurred_at=r.occurred_at,
        reason=r.reason,
        request_id=r.request_id,
    )


class SqlSupportingAccessRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add(self, event: SupportingAccessEvent) -> None:
        self._s.add(
            SupportingAccessEventRow(
                id=event.id,
                tenant_id=event.tenant_id,
                supporting_document_id=event.supporting_document_id,
                actor_employee_id=event.actor_employee_id,
                action=event.action,
                occurred_at=event.occurred_at,
                reason=event.reason,
                request_id=event.request_id,
            )
        )
        await flush_or_conflict(self._s)

    async def list_for_document(self, document_id: UUID) -> builtins.list[SupportingAccessEvent]:
        if self._tenant is None:
            return []
        stmt = (
            select(SupportingAccessEventRow)
            .where(
                SupportingAccessEventRow.tenant_id == self._tenant,
                SupportingAccessEventRow.supporting_document_id == document_id,
            )
            .order_by(SupportingAccessEventRow.occurred_at, SupportingAccessEventRow.id)
        )
        return [_event_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]


def _token_to_domain(r: DownloadTokenRow) -> DownloadToken:
    return DownloadToken(
        token_hash=r.token_hash,
        tenant_id=r.tenant_id,
        supporting_document_id=r.supporting_document_id,
        actor_employee_id=r.actor_employee_id,
        actor_user_id=r.actor_user_id,
        actor_roles=tuple(r.actor_roles or ()),
        expires_at=r.expires_at,
        created_at=r.created_at,
        reason=r.reason,
        used_at=r.used_at,
    )


class SqlDownloadTokenRepository:
    """`platform.supporting_download_tokens` — no RLS; the token hash is the only key the bearer presents."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, token: DownloadToken) -> None:
        self._s.add(
            DownloadTokenRow(
                token_hash=token.token_hash,
                tenant_id=token.tenant_id,
                supporting_document_id=token.supporting_document_id,
                actor_employee_id=token.actor_employee_id,
                actor_user_id=token.actor_user_id,
                actor_roles=list(token.actor_roles),
                expires_at=token.expires_at,
                created_at=token.created_at,
                reason=token.reason,
                used_at=token.used_at,
            )
        )
        await flush_or_conflict(self._s)

    async def consume(self, token_hash: str, now: datetime) -> DownloadToken | None:
        """Atomic single use: the row is marked used only if it is unused and not expired."""
        stmt = (
            update(DownloadTokenRow)
            .where(
                DownloadTokenRow.token_hash == token_hash,
                DownloadTokenRow.used_at.is_(None),
                DownloadTokenRow.expires_at > now,
            )
            .values(used_at=now)
            .returning(DownloadTokenRow)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        # opportunistic sweep of stale tokens (hashes only, but no reason to keep them)
        await self._s.execute(
            delete(DownloadTokenRow).where(DownloadTokenRow.expires_at < now - TOKEN_SWEEP_AGE)
        )
        return _token_to_domain(row) if row else None
