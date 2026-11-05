"""RFC 9457 problem details for every error response (doc 09 §1)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from leave_assistant.application.errors import ApplicationError, AuthorizationError, InvalidInputError
from leave_assistant.domain.errors import DomainError

PROBLEM_TYPE_PREFIX = "urn:leave-assistant:problem:"
PROBLEM_MEDIA_TYPE = "application/problem+json"

_STATUS_TITLES = {
    400: "Bad request",
    401: "Authentication required",
    403: "Authorization denied",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Payload too large",
    415: "Unsupported media type",
    422: "Validation error",
    429: "Rate limited",
    500: "Internal server error",
    503: "Service unavailable",
}
_STATUS_TYPES = {
    400: "bad-request",
    401: "authentication-required",
    403: "authorization-denied",
    404: "not-found",
    405: "method-not-allowed",
    409: "conflict",
    413: "document/too-large",
    415: "document/unsupported-type",
    422: "validation-error",
    429: "rate-limited",
    500: "internal-error",
    503: "service-unavailable",
}
_PROBLEM_STATUS = {
    "authentication-required": 401,
    "authorization-denied": 403,
    "tenant/suspended": 403,
    "not-found": 404,
    "conflict": 409,
    "idempotency-mismatch": 409,
    "version-conflict": 409,
    "department/not-empty": 409,
    "validation-error": 422,
    "leave/invalid-state": 409,
    "approval/step-not-pending": 409,
    "approval/not-permitted": 403,
    "approval/segregation-of-duties": 403,
    "rate-limited": 429,
    "service-unavailable": 503,
    "internal-error": 500,
    "document/unsupported-type": 415,
    "document/too-large": 413,
    "document/not-retrievable": 409,
    "document/invalid-state": 409,
    "document/duplicate-content": 409,
    "document/acl-not-allowed": 422,
    "document/processing-failed": 500,
    "job/not-failed": 409,
}
_PROBLEM_TITLES = {
    "tenant/suspended": "Tenant suspended",
    "leave/invalid-dates": "Invalid dates",
    "leave/overlap": "Overlapping request",
    "leave/insufficient-balance": "Insufficient balance",
    "leave/notice-period": "Notice period not respected",
    "leave/max-consecutive": "Too many consecutive days",
    "leave/retroactive-not-allowed": "Retroactive request not allowed",
    "leave/document-required": "Supporting document required",
    "leave/invalid-state": "Not allowed in the current state",
    "leave/no-approval-rule": "No approval rule configured",
    "approval/step-not-pending": "Step not pending",
    "approval/not-permitted": "Approval not permitted",
    "approval/segregation-of-duties": "Segregation of duties",
    "version-conflict": "Version conflict",
    "idempotency-mismatch": "Idempotency key mismatch",
    "department/not-empty": "Department not empty",
    "document/unsupported-type": "Unsupported document type",
    "document/too-large": "Document too large",
    "document/not-retrievable": "Document version not retrievable",
    "document/invalid-state": "Not allowed in the current document state",
    "document/duplicate-content": "Identical content already uploaded",
    "document/acl-not-allowed": "ACL not allowed",
    "document/processing-failed": "Document processing failed",
    "job/not-failed": "Job is not in a failed state",
}


def status_for(problem_type: str) -> int:
    return _PROBLEM_STATUS.get(problem_type, 422)


def problem(
    request: Request,
    status: int,
    detail: str = "",
    *,
    problem_type: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    type_suffix = problem_type or _STATUS_TYPES.get(status, "error")
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE_PREFIX + type_suffix,
        "title": _PROBLEM_TITLES.get(type_suffix, _STATUS_TITLES.get(status, "Error")),
        "status": status,
        "detail": detail,
        "instance": str(request.url.path),
        "request_id": getattr(request.state, "request_id", None),
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)


async def _audit_denial(request: Request, exc: AuthorizationError) -> None:
    principal = getattr(request.state, "principal", None)
    services = getattr(request.app.state, "services", None)
    if principal is None or services is None:
        return
    try:
        await services.audit.record_denial(
            services.uow_factory, principal, exc.action or "unknown", exc.resource
        )
    except Exception:  # noqa: BLE001 - auditing a denial must never turn a 403 into a 500
        pass


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(request, exc.status_code, str(exc.detail), headers=dict(exc.headers or {}))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        return problem(request, 422, "Request validation failed.", errors=errors)

    @app.exception_handler(ApplicationError)
    async def _application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        status = status_for(exc.problem_type)
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        errors = exc.errors if isinstance(exc, InvalidInputError) else None
        if isinstance(exc, AuthorizationError):
            await _audit_denial(request, exc)
        return problem(
            request, status, exc.detail, problem_type=exc.problem_type, errors=errors, headers=headers
        )

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        field = exc.params.get("field")
        errors = [{"loc": ["body", str(field)], "msg": exc.detail, "type": exc.code}] if field else None
        return problem(request, status_for(exc.code), exc.detail, problem_type=exc.code, errors=errors)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return problem(request, 500, "An unexpected error occurred.")
