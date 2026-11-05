"""Authorization glue for documents: Reader/DocumentScope from principals and rows; 404 for invisible documents."""

from __future__ import annotations

from uuid import UUID

from leave_assistant.application.errors import AuthorizationError, NotFoundError
from leave_assistant.application.ports import UnitOfWork
from leave_assistant.domain.documents.types import Category, Document
from leave_assistant.domain.documents.visibility import (
    DocumentScope,
    Reader,
    can_manage,
    can_read,
    can_review,
    can_upload,
)
from leave_assistant.domain.tenancy import Principal


def reader_for(principal: Principal) -> Reader:
    return Reader(
        roles=principal.roles, employee_id=principal.employee_id, department_id=principal.department_id
    )


def require_tenant_user(principal: Principal, *, action: str) -> tuple[UUID, UUID]:
    if principal.tenant_id is None or principal.employee_id is None:
        raise AuthorizationError("documents are available to company users only", action=action)
    return principal.tenant_id, principal.employee_id


async def scope_for(uow: UnitOfWork, document: Document) -> DocumentScope:
    manager_id: UUID | None = None
    if document.department_id is not None:
        department = await uow.departments.get(document.department_id)
        manager_id = department.manager_employee_id if department else None
    acl = await uow.document_acl.list_for_document(document.id)
    return DocumentScope(
        category=document.category,
        owner_employee_id=document.owner_employee_id,
        department_id=document.department_id,
        department_manager_employee_id=manager_id,
        acl=tuple(acl),
    )


async def load_visible(
    uow: UnitOfWork, principal: Principal, document_id: UUID
) -> tuple[Document, DocumentScope]:
    """Invisible and deleted documents are indistinguishable from unknown ones (no existence leak)."""
    document = await uow.documents.get(document_id)
    if document is None or document.deleted_at is not None:
        raise NotFoundError("document not found")
    scope = await scope_for(uow, document)
    if not can_read(reader_for(principal), scope):
        raise NotFoundError("document not found")
    return document, scope


def require_upload(principal: Principal, category: Category, department_id: UUID | None) -> None:
    if not can_upload(reader_for(principal), category, department_id=department_id):
        raise AuthorizationError(
            f"your roles cannot upload {category.value} documents",
            action="document.upload",
            resource=f"document_category:{category.value}",
        )


def require_manage(principal: Principal, document: Document, scope: DocumentScope) -> None:
    if not can_manage(reader_for(principal), scope):
        raise AuthorizationError(
            "you cannot manage this document", action="document.manage", resource=f"document:{document.id}"
        )


def require_review(principal: Principal, document: Document) -> None:
    if not can_review(reader_for(principal), document.category):
        raise AuthorizationError(
            f"your roles cannot review {document.category.value} documents",
            action="document.review",
            resource=f"document:{document.id}",
        )
