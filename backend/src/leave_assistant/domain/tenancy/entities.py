"""Tenancy entities and value objects (doc 07 §4.1–§4.2). Pure Python; persistence lives in infrastructure."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from leave_assistant.domain.errors import InvalidValue
from leave_assistant.domain.tenancy.roles import Role

SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_slug(slug: str) -> str:
    if not SLUG_PATTERN.match(slug):
        raise InvalidValue(
            "slug must be 3–32 lowercase letters, digits or hyphens, starting and ending alphanumeric",
            params={"field": "slug"},
        )
    return slug


def validate_email(email: str) -> str:
    normalised = email.strip().lower()
    if not _EMAIL_PATTERN.match(normalised) or len(normalised) > 254:
        raise InvalidValue("invalid e-mail address", params={"field": "email"})
    return normalised


class Locale(StrEnum):
    EL = "el"
    EN = "en"


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class EmployeeStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class TenantSettings:
    """Per-tenant configuration stored in `tenants.settings` (doc 07 §4.1). Defaults follow ADR-0028/0029."""

    work_week: tuple[int, ...] = (0, 1, 2, 3, 4)  # ISO weekday numbers 0=Monday … 6=Sunday
    max_upload_mb: int = 10
    conversation_retention_days: int = 90
    proposal_ttl_minutes: int = 15
    allow_hr_fallback: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TenantSettings:
        data = dict(data or {})
        defaults = cls()
        unknown = set(data) - {f for f in cls.__dataclass_fields__}
        if unknown:
            raise InvalidValue(f"unknown settings: {sorted(unknown)}", params={"field": "settings"})
        work_week = tuple(int(d) for d in data.get("work_week", defaults.work_week))
        if not work_week or any(d < 0 or d > 6 for d in work_week) or len(set(work_week)) != len(work_week):
            raise InvalidValue(
                "work_week must list distinct weekdays 0–6", params={"field": "settings.work_week"}
            )
        settings = cls(
            work_week=work_week,
            max_upload_mb=int(data.get("max_upload_mb", defaults.max_upload_mb)),
            conversation_retention_days=int(
                data.get("conversation_retention_days", defaults.conversation_retention_days)
            ),
            proposal_ttl_minutes=int(data.get("proposal_ttl_minutes", defaults.proposal_ttl_minutes)),
            allow_hr_fallback=bool(data.get("allow_hr_fallback", defaults.allow_hr_fallback)),
        )
        for name, lower, upper in (
            ("max_upload_mb", 1, 50),
            ("conversation_retention_days", 1, 3650),
            ("proposal_ttl_minutes", 1, 1440),
        ):
            value = getattr(settings, name)
            if not lower <= value <= upper:
                raise InvalidValue(
                    f"{name} must be between {lower} and {upper}", params={"field": f"settings.{name}"}
                )
        return settings

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_week": list(self.work_week),
            "max_upload_mb": self.max_upload_mb,
            "conversation_retention_days": self.conversation_retention_days,
            "proposal_ttl_minutes": self.proposal_ttl_minutes,
            "allow_hr_fallback": self.allow_hr_fallback,
        }


@dataclass(slots=True)
class Tenant:
    id: UUID
    slug: str
    name: str
    status: TenantStatus
    default_locale: Locale
    timezone: str
    settings: TenantSettings
    created_at: datetime
    updated_at: datetime
    keycloak_group_id: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE


@dataclass(slots=True)
class Department:
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    created_at: datetime
    updated_at: datetime
    manager_employee_id: UUID | None = None


@dataclass(slots=True)
class Employee:
    id: UUID
    tenant_id: UUID
    keycloak_sub: str
    employee_number: str
    first_name: str
    last_name: str
    email: str
    department_id: UUID
    hire_date: date
    locale: Locale
    status: EmployeeStatus
    created_at: datetime
    updated_at: datetime
    roles: frozenset[Role] = field(default_factory=frozenset)
    manager_override_employee_id: UUID | None = None

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_active(self) -> bool:
        return self.status == EmployeeStatus.ACTIVE
