"""Calendar problem types (doc 09 §1 registry; M8)."""

from __future__ import annotations

from leave_assistant.application.errors import ConflictError, InvalidInputError, NotFoundError


class CalendarConnectionExpired(ConflictError):
    problem_type = "calendar/connection-expired"


class CalendarNotConnected(NotFoundError):
    problem_type = "calendar/not-connected"


class OAuthStateInvalid(InvalidInputError):
    problem_type = "calendar/oauth-state-invalid"


class SyncNotRetryable(ConflictError):
    problem_type = "calendar/not-retryable"


class InsufficientScope(InvalidInputError):
    problem_type = "calendar/insufficient-scope"
