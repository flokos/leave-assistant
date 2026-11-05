"""Command and result objects for tenancy use cases (plain dataclasses; API schemas convert to these)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from leave_assistant.domain.tenancy import Department, Employee, Locale, Role


class _Unset:
    def __repr__(self) -> str:  # pragma: no cover
        return "UNSET"


UNSET: Any = _Unset()


@dataclass(frozen=True, slots=True)
class CreateTenant:
    slug: str
    name: str
    default_locale: Locale
    timezone: str
    settings: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class UpdateTenant:
    name: str | None = None
    default_locale: Locale | None = None
    timezone: str | None = None
    settings: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BootstrapAdmin:
    email: str
    first_name: str
    last_name: str
    locale: Locale
    employee_number: str = "ADMIN-001"
    hire_date: date | None = None


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    employee: Employee
    department: Department
    username: str
    temporary_password: str


@dataclass(frozen=True, slots=True)
class CreateEmployee:
    email: str
    first_name: str
    last_name: str
    employee_number: str
    department_id: UUID
    locale: Locale
    hire_date: date
    roles: frozenset[Role]
    manager_override_employee_id: UUID | None = None
    password: str | None = None  # seed only: a known dev password; the API never accepts one
    temporary_password: bool = True


@dataclass(frozen=True, slots=True)
class CreatedEmployee:
    employee: Employee
    temporary_password: str | None


@dataclass(frozen=True, slots=True)
class UpdateEmployee:
    first_name: str | None = None
    last_name: str | None = None
    employee_number: str | None = None
    department_id: UUID | None = None
    locale: Locale | None = None
    hire_date: date | None = None
    manager_override_employee_id: UUID | None = UNSET


@dataclass(frozen=True, slots=True)
class CreateDepartment:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class UpdateDepartment:
    code: str | None = None
    name: str | None = None
