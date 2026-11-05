"""The trusted Principal built per request from the validated token and the employee record (doc 08 §5.1)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from leave_assistant.domain.tenancy.entities import Locale
from leave_assistant.domain.tenancy.roles import Role


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str  # Keycloak `sub`
    username: str
    roles: frozenset[Role]
    tenant_id: UUID | None = None
    employee_id: UUID | None = None
    department_id: UUID | None = None
    locale: Locale = Locale.EN
    display_name: str = ""

    @property
    def is_platform_admin(self) -> bool:
        return Role.PLATFORM_ADMIN in self.roles

    @property
    def is_automation(self) -> bool:
        return Role.AUTOMATION in self.roles

    @property
    def is_tenant_user(self) -> bool:
        return self.tenant_id is not None and self.employee_id is not None

    def has_any(self, *roles: Role) -> bool:
        return any(role in self.roles for role in roles)
