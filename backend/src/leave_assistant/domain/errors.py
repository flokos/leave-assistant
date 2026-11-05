"""Domain rule violations. The API maps them to RFC 9457 problems (doc 09 §1); services never catch them silently."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class. `code` is the problem-type suffix (e.g. `validation-error`, `leave/overlap`)."""

    code = "domain-error"

    def __init__(
        self, detail: str = "", *, code: str | None = None, params: dict[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code or type(self).code
        self.params: dict[str, Any] = params or {}


class InvalidValue(DomainError):
    """A value object or command field violates a domain rule."""

    code = "validation-error"
