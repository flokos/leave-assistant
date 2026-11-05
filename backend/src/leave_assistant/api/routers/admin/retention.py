"""Retention (doc 09 §2.8, Q-08): company administrators manage policies; HR decides review items."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from leave_assistant.api.deps import AuditReader, CompanyAdmin, CurrentServices, require_tenant_roles
from leave_assistant.api.idempotency import IdempotentRoute
from leave_assistant.api.schemas.retention import (
    RetentionDecisionIn,
    RetentionItemOut,
    RetentionPolicyCreate,
    RetentionPolicyOut,
    RetentionPolicyUpdate,
    ReviewStatusField,
)
from leave_assistant.application.retention.service import CreateRetentionPolicy, UpdateRetentionPolicy
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.domain.retention.eligibility import DocumentClass, RetentionAction, ReviewStatus
from leave_assistant.domain.tenancy import Principal, Role

router = APIRouter(prefix="/admin", tags=["admin"], route_class=IdempotentRoute)
HrAdmin = Annotated[Principal, Depends(require_tenant_roles(Role.HR_ADMIN))]


@router.get("/retention-policies", response_model=list[RetentionPolicyOut])
async def list_retention_policies(
    principal: AuditReader, services: CurrentServices
) -> list[RetentionPolicyOut]:
    return [RetentionPolicyOut.from_domain(p) for p in await services.retention.list_policies(principal)]


@router.post("/retention-policies", response_model=RetentionPolicyOut, status_code=status.HTTP_201_CREATED)
async def create_retention_policy(
    body: RetentionPolicyCreate, principal: CompanyAdmin, services: CurrentServices
) -> RetentionPolicyOut:
    policy = await services.retention.create_policy(
        principal,
        CreateRetentionPolicy(
            document_class=DocumentClass(body.document_class),
            retention_days=body.retention_days,
            action=RetentionAction(body.action),
            enabled=body.enabled,
        ),
    )
    return RetentionPolicyOut.from_domain(policy)


@router.patch("/retention-policies/{policy_id}", response_model=RetentionPolicyOut)
async def update_retention_policy(
    policy_id: UUID, body: RetentionPolicyUpdate, principal: CompanyAdmin, services: CurrentServices
) -> RetentionPolicyOut:
    policy = await services.retention.update_policy(
        principal,
        policy_id,
        UpdateRetentionPolicy(
            retention_days=body.retention_days if body.retention_days is not None else UNSET,
            action=RetentionAction(body.action) if body.action is not None else UNSET,
            enabled=body.enabled if body.enabled is not None else UNSET,
        ),
    )
    return RetentionPolicyOut.from_domain(policy)


@router.delete("/retention-policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_retention_policy(
    policy_id: UUID, principal: CompanyAdmin, services: CurrentServices
) -> Response:
    await services.retention.delete_policy(principal, policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/retention/review-items", response_model=list[RetentionItemOut])
async def list_review_items(
    principal: HrAdmin,
    services: CurrentServices,
    status_filter: Annotated[ReviewStatusField | None, Query(alias="status")] = "pending",
) -> list[RetentionItemOut]:
    items = await services.retention.list_items(
        principal, status=ReviewStatus(status_filter) if status_filter else None
    )
    return [RetentionItemOut.from_domain(i) for i in items]


@router.post("/retention/review-items/{item_id}/decide", response_model=RetentionItemOut)
async def decide_review_item(
    item_id: UUID, body: RetentionDecisionIn, principal: HrAdmin, services: CurrentServices
) -> RetentionItemOut:
    item = await services.retention.decide(principal, item_id, ReviewStatus(body.decision), body.comment)
    return RetentionItemOut.from_domain(item)
