"""Application-level errors. `problem_type` is the RFC 9457 type suffix the API emits (doc 09 §1)."""

from __future__ import annotations

from typing import Any


class ApplicationError(Exception):
    problem_type = "internal-error"

    def __init__(self, detail: str = "", *, problem_type: str | None = None, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.problem_type = problem_type or type(self).problem_type
        self.extra = extra


class AuthenticationError(ApplicationError):
    """The bearer token is missing, malformed, expired, forged or carries an unusable tenant claim."""

    problem_type = "authentication-required"


class AuthorizationError(ApplicationError):
    """The principal is authenticated but the policy denies the action (audited as `authz.denied`)."""

    problem_type = "authorization-denied"

    def __init__(
        self, detail: str = "", *, action: str = "", resource: str | None = None, **extra: Any
    ) -> None:
        super().__init__(detail, action=action, resource=resource, **extra)
        self.action = action
        self.resource = resource


class TenantSuspendedError(ApplicationError):
    problem_type = "tenant/suspended"


class NotFoundError(ApplicationError):
    """Also used for resources that RLS hides: cross-tenant identifiers are indistinguishable from unknown ones."""

    problem_type = "not-found"


class ConflictError(ApplicationError):
    problem_type = "conflict"


class InvalidInputError(ApplicationError):
    problem_type = "validation-error"

    def __init__(self, detail: str = "", *, errors: list[dict[str, Any]] | None = None, **extra: Any) -> None:
        super().__init__(detail, **extra)
        self.errors = errors or []


class DependencyError(ApplicationError):
    """An external dependency (identity provider) failed; the operation was not completed."""

    problem_type = "service-unavailable"
