from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum


class DocumentClass(StrEnum):
    SUPPORTING_DOCUMENT = "supporting_document"
    COMPANY_DOCUMENT = "company_document"
    CONVERSATION = "conversation"
    LEAVE_RECORD = "leave_record"


class RetentionAction(StrEnum):
    NOTIFY_ONLY = "notify_only"
    DELETE_AFTER_REVIEW = "delete_after_review"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED_DELETE = "approved_delete"
    RETAINED = "retained"
    DELETED = "deleted"


NEVER_DELETED: frozenset[DocumentClass] = frozenset({DocumentClass.LEAVE_RECORD})
MAX_RETENTION_DAYS = 3650


def eligible_on(reference: date, retention_days: int) -> date:
    """The first day an item may be deleted: the class's reference date plus the policy's retention days."""
    if retention_days < 0 or retention_days > MAX_RETENTION_DAYS:
        raise ValueError("retention_days must be between 0 and 3650")
    return reference + timedelta(days=retention_days)


def is_eligible(reference: date, retention_days: int, today: date) -> bool:
    return today >= eligible_on(reference, retention_days)
