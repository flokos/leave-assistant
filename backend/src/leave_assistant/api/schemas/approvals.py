from __future__ import annotations

from uuid import UUID

from pydantic import Field

from leave_assistant.api.schemas.common import ApiModel
from leave_assistant.api.schemas.leave import LeaveRequestSummary, StepOut
from leave_assistant.application.leave.views import PendingItem


class RequesterOut(ApiModel):
    id: UUID
    display_name: str
    employee_number: str
    department_id: UUID


class PendingItemOut(ApiModel):
    step: StepOut
    request: LeaveRequestSummary
    requester: RequesterOut
    reason: str | None
    fallback: bool
    supporting_documents: int = 0

    @classmethod
    def from_view(cls, item: PendingItem) -> PendingItemOut:
        return cls(
            step=StepOut.from_domain(item.step),
            request=LeaveRequestSummary.from_domain(item.request, item.leave_type.code),
            requester=RequesterOut(
                id=item.requester.id,
                display_name=item.requester.display_name,
                employee_number=item.requester.employee_number,
                department_id=item.requester.department_id,
            ),
            reason=item.request.reason if item.reason_visible else None,
            fallback=item.fallback,
            supporting_documents=item.supporting_count,
        )


class DecisionIn(ApiModel):
    version: int = Field(ge=1, description="The step version you read (409 version-conflict when stale)")
    comment: str | None = Field(default=None, max_length=1000)
