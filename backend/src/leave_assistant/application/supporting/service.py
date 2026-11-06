"""SupportingDocumentService (doc 08 §9, doc 09 §2.3/§2.6).

Owner employee (own request) or an HR administrator may attach; owner and HR may read metadata; downloads go
through a 60-second single-use token; every access is an access event plus an audit entry. Managers and company
administrators see nothing (404). Files are encrypted before storage (per-file DEK wrapped by `APP_KEK`). The
service has no path to parsing, chunking or embedding — the type system keeps it that way.
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.documents.commands import Upload
from leave_assistant.application.errors import (
    AuthorizationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    SecurityNotConfigured,
)
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.ports import (
    Clock,
    DocumentStorage,
    DownloadToken,
    FileCipher,
    IdGenerator,
    SupportingAccessEvent,
    SupportingDocument,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.request_context import current_request_id
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.formats import TooLarge, UnsupportedType, sanitize_filename
from leave_assistant.domain.leave.request import LeaveRequest
from leave_assistant.domain.leave.state_machine import TERMINAL_STATUSES, RequestStatus
from leave_assistant.domain.tenancy import Principal, Role

ALLOWED: dict[str, str] = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}
CLOSED_STATUSES = TERMINAL_STATUSES - {RequestStatus.COMPLETED}


def supporting_key(tenant_id: UUID, request_id: UUID, document_id: UUID) -> str:
    return f"tenants/{tenant_id}/supporting/{request_id}/{document_id}"


def sniff_supporting(head: bytes, filename: str | None) -> str:
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    raise UnsupportedType(
        "supporting documents must be PDF, JPEG or PNG",
        code="supporting/unsupported-type",
        params={"filename": filename or ""},
    )


@dataclass(frozen=True, slots=True)
class SupportingDocumentDetail:
    document: SupportingDocument
    access_events: list[SupportingAccessEvent]  # HR only; empty for the owner


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    expires_at: object


class SupportingDocumentService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: DocumentStorage,
        cipher: FileCipher,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        notifications: NotificationService,
        *,
        max_upload_mb: int = 10,
        token_ttl_seconds: int = 60,
    ) -> None:
        self._uow = uow_factory
        self._storage = storage
        self._cipher = cipher
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._notifications = notifications
        self._max_bytes = max_upload_mb * 1024 * 1024
        self._ttl = timedelta(seconds=token_ttl_seconds)

    @property
    def configured(self) -> bool:
        return self._cipher.configured

    # --- access matrix --------------------------------------------------------------------------------------
    @staticmethod
    def _is_hr(principal: Principal) -> bool:
        return Role.HR_ADMIN in principal.roles

    async def _request_for(self, uow: UnitOfWork, principal: Principal, request_id: UUID) -> LeaveRequest:
        request = await uow.leave_requests.get(request_id)
        if request is None or not (request.employee_id == principal.employee_id or self._is_hr(principal)):
            raise NotFoundError("request not found")  # managers / company administrators: no existence leak
        return request

    async def _document_for(
        self, uow: UnitOfWork, principal: Principal, document_id: UUID
    ) -> SupportingDocument:
        document = await uow.supporting_documents.get(document_id)
        if document is None or document.status == "deleted":
            raise NotFoundError("supporting document not found")
        if not (document.employee_id == principal.employee_id or self._is_hr(principal)):
            raise NotFoundError("supporting document not found")
        return document

    async def _access(
        self,
        uow: UnitOfWork,
        principal: Principal,
        document: SupportingDocument,
        action: str,
        reason: str | None,
    ) -> None:
        assert principal.employee_id is not None
        await uow.supporting_access.add(
            SupportingAccessEvent(
                id=self._ids.new_id(),
                tenant_id=document.tenant_id,
                supporting_document_id=document.id,
                actor_employee_id=principal.employee_id,
                action=action,
                occurred_at=self._clock.now(),
                reason=reason,
                request_id=current_request_id(),
            )
        )
        await self._audit.record(
            uow,
            principal,
            f"supporting.{action}",
            resource_type="supporting_document",
            resource_id=document.id,
            details={
                "request_id": str(document.request_id),
                "reason": reason,
                "owner": str(document.employee_id),
            },
        )

    # --- upload ---------------------------------------------------------------------------------------------
    async def upload(self, principal: Principal, request_id: UUID, upload: Upload) -> SupportingDocument:
        if principal.tenant_id is None or principal.employee_id is None:
            raise AuthorizationError("company users only", action="supporting.upload")
        if not self._cipher.configured:
            raise SecurityNotConfigured("APP_KEK is not configured; supporting documents cannot be stored")
        if upload.size_bytes <= 0:
            raise UnsupportedType("the uploaded file is empty", code="supporting/unsupported-type")
        if upload.size_bytes > self._max_bytes:
            raise TooLarge(
                f"supporting documents are limited to {self._max_bytes // (1024 * 1024)} MB",
                params={"max_mb": self._max_bytes // (1024 * 1024)},
            )
        data = await asyncio.to_thread(upload.path.read_bytes)
        mime = sniff_supporting(data[:16], upload.filename)
        digest = hashlib.sha256(data).hexdigest()
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            request = await self._request_for(uow, principal, request_id)
            if request.status in CLOSED_STATUSES:
                raise ConflictError("the request is closed", problem_type="leave/invalid-state")
            document_id = self._ids.new_id()
            key = supporting_key(principal.tenant_id, request.id, document_id)
            ciphertext, wrapped = self._cipher.encrypt(data, aad=key.encode())
            await self._storage.put_bytes(key, ciphertext, content_type="application/octet-stream")
            document = SupportingDocument(
                id=document_id,
                tenant_id=principal.tenant_id,
                request_id=request.id,
                employee_id=request.employee_id,
                object_key=key,
                original_filename=sanitize_filename(upload.filename, extension=ALLOWED[mime]),
                mime=mime,
                size_bytes=upload.size_bytes,
                sha256=digest,
                encrypted_dek=wrapped,
                status="stored",
                uploaded_by_employee_id=principal.employee_id,
                created_at=now,
            )
            await uow.supporting_documents.add(document)
            await self._audit.record(
                uow,
                principal,
                "supporting.uploaded",
                resource_type="supporting_document",
                resource_id=document.id,
                details={
                    "request_id": str(request.id),
                    "owner": str(request.employee_id),
                    "on_behalf": principal.employee_id != request.employee_id,
                    "mime": mime,
                    "size_bytes": upload.size_bytes,
                    "sha256": digest,
                },
            )
            hr_admins = await uow.employees.list_active(role=Role.HR_ADMIN)
            await self._notifications.notify(
                uow,
                [e.id for e in hr_admins if e.id != principal.employee_id],
                "supporting.uploaded",
                {
                    "request_id": str(request.id),
                    "request_number": request.request_number,
                    "filename": document.original_filename,
                },
                dedupe_key=f"supporting.uploaded:{document.id}",
                channels=("in_app",),  # sick-leave evidence: never by e-mail (M8)
            )
            await uow.commit()
        return document

    # --- reads ----------------------------------------------------------------------------------------------
    async def list_for_request(
        self, principal: Principal, request_id: UUID
    ) -> builtins.list[SupportingDocument]:
        if principal.tenant_id is None:
            raise AuthorizationError("company users only", action="supporting.view")
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await self._request_for(uow, principal, request_id)
            documents = await uow.supporting_documents.list_for_request(request_id)
        return [d for d in documents if d.status != "deleted"]

    async def get(self, principal: Principal, document_id: UUID) -> SupportingDocumentDetail:
        if principal.tenant_id is None:
            raise AuthorizationError("company users only", action="supporting.view")
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document = await self._document_for(uow, principal, document_id)
            await self._access(uow, principal, document, "view", None)
            events = (
                await uow.supporting_access.list_for_document(document.id) if self._is_hr(principal) else []
            )
            await uow.commit()
        return SupportingDocumentDetail(document=document, access_events=events)

    async def issue_download_token(
        self, principal: Principal, document_id: UUID, reason: str | None
    ) -> IssuedToken:
        if principal.tenant_id is None:
            raise AuthorizationError("company users only", action="supporting.download")
        cleaned = " ".join(reason.split()) if reason else None
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document = await self._document_for(uow, principal, document_id)
            if self._is_hr(principal) and document.employee_id != principal.employee_id and not cleaned:
                raise InvalidInputError(
                    "HR downloads require a reason",
                    errors=[{"loc": ["body", "reason"], "msg": "required for HR access"}],
                )
            token = secrets.token_urlsafe(32)
            assert principal.employee_id is not None
            await uow.download_tokens.add(
                DownloadToken(
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    tenant_id=document.tenant_id,
                    supporting_document_id=document.id,
                    actor_employee_id=principal.employee_id,
                    actor_user_id=principal.user_id,
                    actor_roles=tuple(sorted(r.value for r in principal.roles)),
                    expires_at=now + self._ttl,
                    created_at=now,
                    reason=cleaned,
                )
            )
            await self._access(uow, principal, document, "download_token", cleaned)
            await uow.commit()
        return IssuedToken(token=token, expires_at=now + self._ttl)

    async def download(self, token: str) -> tuple[SupportingDocument, bytes]:
        """Token bearer (no bearer JWT): single use, 60 s; the actor recorded on the token is audited."""
        now = self._clock.now()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        async with self._uow(None) as uow:
            record = await uow.download_tokens.consume(token_hash, now)
            await uow.commit()
        if record is None:
            raise NotFoundError("download link expired or already used")
        actor = Principal(
            user_id=record.actor_user_id,
            username=record.actor_user_id,
            roles=frozenset(Role(r) for r in record.actor_roles),
            tenant_id=record.tenant_id,
            employee_id=record.actor_employee_id,
        )
        async with self._uow(TenantContext.for_principal(actor)) as uow:
            document = await uow.supporting_documents.get(record.supporting_document_id)
            if document is None or document.status == "deleted":
                raise NotFoundError("supporting document not found")
            ciphertext = await self._storage.get_bytes(document.object_key)
            plaintext = self._cipher.decrypt(
                ciphertext, document.encrypted_dek, aad=document.object_key.encode()
            )
            await self._access(uow, actor, document, "download", record.reason)
            await uow.commit()
        return document, plaintext

    async def delete(self, principal: Principal, document_id: UUID, reason: str) -> SupportingDocument:
        if not self._is_hr(principal) or principal.tenant_id is None:
            raise AuthorizationError(
                "only HR administrators delete supporting documents", action="supporting.delete"
            )
        cleaned = " ".join(reason.split())
        if not cleaned:
            raise InvalidInputError(
                "a reason is required", errors=[{"loc": ["body", "reason"], "msg": "required"}]
            )
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            document = await self._document_for(uow, principal, document_id)
            document.status = "deleted"
            document.deleted_at = now
            await uow.supporting_documents.update(document)
            await self._access(uow, principal, document, "delete", cleaned)
            await self._storage.delete(document.object_key)  # before the commit: a failure changes nothing
            await uow.commit()
        return document
