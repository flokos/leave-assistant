"""`Idempotency-Key` enforcement for state-changing routes (doc 09 §1).

`IdempotentRoute` wraps the route handler: the key is required on POST/PUT/PATCH/DELETE; a replay with the same
body returns the stored response (`Idempotent-Replayed: true`); a different body under the same key is a
`409 idempotency-mismatch`. Keys are private to the caller (`user_id`) and expire after `idempotency_ttl_hours`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

from leave_assistant.api.deps import get_services, get_settings, resolve_principal
from leave_assistant.application.errors import ConflictError, InvalidInputError
from leave_assistant.application.idempotency import IdempotencyRecord
from leave_assistant.application.tenancy.context import TenantContext

HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
NO_IDEMPOTENCY = {
    "x-idempotency": "none"
}  # openapi_extra marker for side-effect-free POSTs (validate, simulate)


class IdempotentRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()
        route_name = f"{next(iter(sorted(self.methods or {'POST'})))} {self.path_format}"

        exempt = (self.openapi_extra or {}).get("x-idempotency") == "none"

        async def handler(request: Request) -> Response:
            if request.method not in MUTATING_METHODS or exempt:
                return await original(request)
            key = request.headers.get(HEADER, "").strip()
            if not key or len(key) > 128:
                raise InvalidInputError(
                    f"{HEADER} header is required on state-changing requests",
                    errors=[
                        {
                            "loc": ["header", HEADER],
                            "msg": "required, at most 128 characters",
                            "type": "missing",
                        }
                    ],
                )
            services = get_services(request)
            settings = get_settings(request)
            principal = await resolve_principal(request)
            body = await request.body()
            request_hash = hashlib.sha256(request.url.path.encode() + b"\n" + body).hexdigest()
            context = TenantContext.for_principal(principal)
            now = services.clock.now()
            async with services.uow_factory(context) as uow:
                existing = await uow.idempotency.get(principal.user_id, route_name, key)
            if existing is not None and existing.expires_at > now:
                if existing.request_hash != request_hash:
                    raise ConflictError(
                        f"{HEADER} was already used with a different request",
                        problem_type="idempotency-mismatch",
                    )
                return JSONResponse(
                    existing.response_body,
                    status_code=existing.response_status,
                    headers={REPLAYED_HEADER: "true"},
                )

            response = await original(request)
            media_type = (response.headers.get("content-type") or "").split(";")[0].strip()
            if 200 <= response.status_code < 500 and media_type == "application/json":
                raw = getattr(response, "body", b"")
                stored_body = json.loads(bytes(raw)) if raw else None
                record = IdempotencyRecord(
                    id=services.ids.new_id(),
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    route=route_name,
                    key=key,
                    request_hash=request_hash,
                    response_status=response.status_code,
                    response_body=stored_body,
                    created_at=now,
                    expires_at=now + timedelta(hours=settings.idempotency_ttl_hours),
                )
                try:
                    async with services.uow_factory(context) as uow:
                        await uow.idempotency.add(record)
                        await uow.commit()
                except ConflictError:
                    pass  # a concurrent request with the same key stored first; this response is equivalent
            return response

        return handler
