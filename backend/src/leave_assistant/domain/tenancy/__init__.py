"""Tenancy domain: tenants, departments, employees, roles and the trusted Principal (doc 07 §4.1–§4.2, doc 08 §5)."""

from leave_assistant.domain.tenancy.entities import (
    SLUG_PATTERN,
    Department,
    Employee,
    EmployeeStatus,
    Locale,
    Tenant,
    TenantSettings,
    TenantStatus,
    validate_slug,
)
from leave_assistant.domain.tenancy.principal import Principal
from leave_assistant.domain.tenancy.roles import ALL_ROLES, TENANT_ROLES, Role, parse_roles

__all__ = [
    "ALL_ROLES",
    "SLUG_PATTERN",
    "TENANT_ROLES",
    "Department",
    "Employee",
    "EmployeeStatus",
    "Locale",
    "Principal",
    "Role",
    "Tenant",
    "TenantSettings",
    "TenantStatus",
    "parse_roles",
    "validate_slug",
]
