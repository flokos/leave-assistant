"""Retention schemas (doc 09 §2.8) — M5."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.application.ports import RetentionPolicy, RetentionReviewItem

DocumentClassField = Literal["supporting_document", "company_document", "conversation", "leave_record"]
RetentionActionField = Literal["notify_only", "delete_after_review"]
ReviewStatusField = Literal["pending", "approved_delete", "retained", "deleted"]
DecisionField = Literal["approved_delete", "retained"]


class RetentionPolicyOut(ApiModel):
    id: UUID
    document_class: str
    retention_days: int
    action: str
    enabled: bool
    updated_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, p: RetentionPolicy) -> RetentionPolicyOut:
        return cls(
            id=p.id,
            document_class=p.document_class.value,
            retention_days=p.retention_days,
            action=p.action.value,
            enabled=p.enabled,
            updated_by=p.updated_by,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class RetentionPolicyCreate(ApiModel):
    document_class: DocumentClassField
    retention_days: int = Field(ge=0, le=3650)
    action: RetentionActionField = "notify_only"
    enabled: bool = True


class RetentionPolicyUpdate(ApiModel):
    retention_days: int | None = Field(default=None, ge=0, le=3650)
    action: RetentionActionField | None = None
    enabled: bool | None = None


class RetentionItemOut(ApiModel):
    id: UUID
    policy_id: UUID
    subject_type: str
    subject_id: UUID
    label: str
    eligible_since: date
    status: str
    created_at: datetime
    reviewer_employee_id: UUID | None
    decided_at: datetime | None
    comment: str | None

    @classmethod
    def from_domain(cls, i: RetentionReviewItem) -> RetentionItemOut:
        return cls(
            id=i.id,
            policy_id=i.policy_id,
            subject_type=i.subject_type,
            subject_id=i.subject_id,
            label=i.label,
            eligible_since=i.eligible_since,
            status=i.status.value,
            created_at=i.created_at,
            reviewer_employee_id=i.reviewer_employee_id,
            decided_at=i.decided_at,
            comment=i.comment,
        )


class RetentionDecisionIn(ApiModel):
    decision: DecisionField
    comment: str | None = Field(default=None, max_length=1000)
