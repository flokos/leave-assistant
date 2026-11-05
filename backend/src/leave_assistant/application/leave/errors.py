"""Leave-specific application errors mapped to doc 09 problem types."""

from __future__ import annotations

from leave_assistant.application.errors import AuthorizationError, ConflictError, InvalidInputError
from leave_assistant.domain.leave.validation import ValidationReport


class LeaveValidationError(InvalidInputError):
    """The request violates leave rules; `problem_type` is the first violation code, `errors` lists them all."""

    def __init__(self, report: ValidationReport) -> None:
        first = report.violations[0]
        super().__init__(
            first.detail,
            problem_type=first.code,
            errors=[{"code": v.code, "msg": v.detail, "params": dict(v.params)} for v in report.violations],
        )
        self.report = report


class StepNotPending(ConflictError):
    problem_type = "approval/step-not-pending"


class VersionConflict(ConflictError):
    problem_type = "version-conflict"


class ApprovalNotPermitted(AuthorizationError):
    problem_type = "approval/not-permitted"


class SegregationOfDuties(AuthorizationError):
    problem_type = "approval/segregation-of-duties"
