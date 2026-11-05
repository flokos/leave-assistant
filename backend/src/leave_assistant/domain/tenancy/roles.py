"""The five application roles plus the automation service role (brief §4.5, ADR-0004)."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Final


class Role(StrEnum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    HR_ADMIN = "hr_admin"
    COMPANY_ADMIN = "company_admin"
    PLATFORM_ADMIN = "platform_admin"
    AUTOMATION = "automation"


TENANT_ROLES: Final[frozenset[Role]] = frozenset(
    {Role.EMPLOYEE, Role.MANAGER, Role.HR_ADMIN, Role.COMPANY_ADMIN}
)
ALL_ROLES: Final[frozenset[Role]] = frozenset(Role)


def parse_roles(values: Iterable[str]) -> frozenset[Role]:
    """Keep only roles this application knows; Keycloak default roles (e.g. `offline_access`) are ignored."""
    known = {role.value for role in Role}
    return frozenset(Role(value) for value in values if value in known)
