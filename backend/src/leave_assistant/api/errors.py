"""RFC 9457 problem details for every error response (doc 09 §1)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

PROBLEM_TYPE_PREFIX = "urn:leave-assistant:problem:"
PROBLEM_MEDIA_TYPE = "application/problem+json"

_STATUS_TITLES = {
    400: "Bad request",
    401: "Authentication required",
    403: "Authorization denied",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
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
    415: "document/unsupported-type",
    422: "validation-error",
    429: "rate-limited",
    500: "internal-error",
    503: "service-unavailable",
}


def problem(
    request: Request,
    status: int,
    detail: str = "",
    *,
    problem_type: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE_PREFIX + (problem_type or _STATUS_TYPES.get(status, "error")),
        "title": _STATUS_TITLES.get(status, "Error"),
        "status": status,
        "detail": detail,
        "instance": str(request.url.path),
        "request_id": getattr(request.state, "request_id", None),
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(request, exc.status_code, str(exc.detail))

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

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return problem(request, 500, "An unexpected error occurred.")
