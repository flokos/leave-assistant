"""Tenant provisioning (FR-TEN-07, Q-12): Keycloak group + tenant row; first Company Administrator bootstrap."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, ResourceRef, require
from leave_assistant.application.errors import ConflictError, NotFoundError
from leave_assistant.application.pagination import Page, build_page, clamp_limit, decode_cursor
from leave_assistant.application.ports import (
    Clock,
    IdentityAdmin,
    IdGenerator,
    NewIdentityUser,
    UnitOfWorkFactory,
)
from leave_assistant.application.security.policy import SecurityPolicyService
from leave_assistant.application.tenancy.commands import (
    BootstrapAdmin,
    BootstrapResult,
    CreateTenant,
    UpdateTenant,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.application.tenancy.passwords import generate_temporary_password
from leave_assistant.application.tenancy.validation import validate_name, validate_timezone
from leave_assistant.domain.tenancy import (
    Department,
    Employee,
    EmployeeStatus,
    Principal,
    Role,
    Tenant,
    TenantSettings,
    TenantStatus,
    validate_slug,
)
from leave_assistant.domain.tenancy.entities import validate_email

BOOTSTRAP_DEPARTMENT_CODE = "ADMIN"
BOOTSTRAP_DEPARTMENT_NAMES = {"el": "Διοίκηση", "en": "Administration"}
BOOTSTRAP_ROLES = frozenset({Role.COMPANY_ADMIN, Role.EMPLOYEE})


class TenantProvisioningService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        identity: IdentityAdmin,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        *,
        security_policies: SecurityPolicyService | None = None,
    ) -> None:
        self._uow = uow_factory
        self._identity = identity
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._policies = security_policies

    async def list_tenants(
        self, principal: Principal, *, limit: int | None, cursor: str | None
    ) -> Page[Tenant]:
        require(principal, Action.MANAGE_TENANTS)
        size = clamp_limit(limit)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            rows = await uow.tenants.list(limit=size + 1, after=decode_cursor(cursor))
        return build_page(rows, size, lambda t: (t.created_at, t.id))

    async def get_tenant(self, principal: Principal, tenant_id: UUID) -> Tenant:
        require(principal, Action.MANAGE_TENANTS, ResourceRef("tenant", tenant_id))
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            tenant = await uow.tenants.get(tenant_id)
        if tenant is None:
            raise NotFoundError("tenant not found")
        return tenant

    async def create_tenant(self, principal: Principal, cmd: CreateTenant) -> Tenant:
        require(principal, Action.MANAGE_TENANTS)
        tenant, created = await self.ensure_tenant(principal, cmd, allow_existing=False)
        return tenant

    async def ensure_tenant(
        self, principal: Principal, cmd: CreateTenant, *, allow_existing: bool
    ) -> tuple[Tenant, bool]:
        """Create the tenant or (seed) return the existing one by slug. Keycloak group first, row second."""
        require(principal, Action.MANAGE_TENANTS)
        slug = validate_slug(cmd.slug)
        name = validate_name(cmd.name, "name")
        timezone = validate_timezone(cmd.timezone)
        settings = TenantSettings.from_dict(cmd.settings)
        context = TenantContext.for_principal(principal)
        async with self._uow(context) as uow:
            existing = await uow.tenants.get_by_slug(slug)
        if existing is not None:
            if not allow_existing:
                raise ConflictError(f"tenant slug {slug!r} already exists")
            # Re-verify the Keycloak group every time (idempotent): a re-imported realm has no groups yet.
            group_id = await self._identity.ensure_tenant_group(slug, existing.id)
            if existing.keycloak_group_id != group_id:
                existing.keycloak_group_id = group_id
                existing.updated_at = self._clock.now()
                async with self._uow(context) as uow:
                    await uow.tenants.update(existing)
                    await uow.commit()
            return existing, False
        now = self._clock.now()
        tenant = Tenant(
            id=self._ids.new_id(),
            slug=slug,
            name=name,
            status=TenantStatus.ACTIVE,
            default_locale=cmd.default_locale,
            timezone=timezone,
            settings=settings,
            created_at=now,
            updated_at=now,
        )
        tenant.keycloak_group_id = await self._identity.ensure_tenant_group(slug, tenant.id)
        async with self._uow(context) as uow:
            await uow.tenants.add(tenant)
            await self._audit.record(
                uow,
                principal,
                "admin.tenant.created",
                resource_type="tenant",
                resource_id=tenant.id,
                details={"slug": slug, "default_locale": cmd.default_locale, "timezone": timezone},
            )
            await uow.commit()
        await self.ensure_security_policy(context, tenant.id, updated_by=principal.user_id)
        return tenant, True

    async def ensure_security_policy(
        self, context: TenantContext, tenant_id: UUID, *, updated_by: str
    ) -> None:
        """M5: every tenant carries a `security_policies` row copied from the platform defaults (doc 08 §8.2)."""
        if self._policies is None:
            return
        async with self._uow(context.with_tenant(tenant_id)) as uow:
            await self._policies.ensure_policy(uow, tenant_id, updated_by=updated_by)
            await uow.commit()

    async def update_tenant(self, principal: Principal, tenant_id: UUID, cmd: UpdateTenant) -> Tenant:
        tenant = await self.get_tenant(principal, tenant_id)
        changes: dict[str, object] = {}
        if cmd.name is not None:
            tenant.name = validate_name(cmd.name, "name")
            changes["name"] = tenant.name
        if cmd.default_locale is not None:
            tenant.default_locale = cmd.default_locale
            changes["default_locale"] = cmd.default_locale
        if cmd.timezone is not None:
            tenant.timezone = validate_timezone(cmd.timezone)
            changes["timezone"] = tenant.timezone
        if cmd.settings is not None:
            tenant.settings = TenantSettings.from_dict({**tenant.settings.to_dict(), **cmd.settings})
            changes["settings"] = tenant.settings.to_dict()
        if not changes:
            return tenant
        tenant.updated_at = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await uow.tenants.update(tenant)
            await self._audit.record(
                uow,
                principal,
                "admin.tenant.config.changed",
                resource_type="tenant",
                resource_id=tenant.id,
                details={"changes": changes},
            )
            await uow.commit()
        return tenant

    async def set_status(self, principal: Principal, tenant_id: UUID, status: TenantStatus) -> Tenant:
        tenant = await self.get_tenant(principal, tenant_id)
        if tenant.status == status:
            return tenant
        tenant.status = status
        tenant.updated_at = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await uow.tenants.update(tenant)
            await self._audit.record(
                uow,
                principal,
                "admin.tenant.config.changed",
                resource_type="tenant",
                resource_id=tenant.id,
                details={"changes": {"status": status}},
            )
            await uow.commit()
        return tenant

    async def bootstrap_admin(
        self, principal: Principal, tenant_id: UUID, cmd: BootstrapAdmin
    ) -> BootstrapResult:
        """Create the first Company Administrator with a temporary password (forced change at first login)."""
        tenant = await self.get_tenant(principal, tenant_id)
        if not tenant.is_active:
            raise ConflictError("tenant is suspended", problem_type="tenant/suspended")
        email = validate_email(cmd.email)
        first_name = validate_name(cmd.first_name, "first_name", max_length=80)
        last_name = validate_name(cmd.last_name, "last_name", max_length=80)
        tenant_context = TenantContext.for_principal(principal).with_tenant(tenant.id)

        async with self._uow(tenant_context) as uow:
            if await uow.employees.get_by_email(email) is not None:
                raise ConflictError("an employee with this e-mail already exists")
            department = await uow.departments.get_by_code(BOOTSTRAP_DEPARTMENT_CODE)
        if await self._identity.find_user_by_username(email) is not None:
            raise ConflictError("a user with this e-mail already exists in the identity provider")

        temporary_password = generate_temporary_password()
        sub = await self._identity.create_user(
            NewIdentityUser(
                username=email,
                email=email,
                first_name=first_name,
                last_name=last_name,
                locale=cmd.locale.value,
                password=temporary_password,
                temporary_password=True,
                realm_roles=frozenset(r.value for r in BOOTSTRAP_ROLES),
                group_path=f"/tenants/{tenant.slug}",
            )
        )
        now = self._clock.now()
        try:
            async with self._uow(tenant_context) as uow:
                if department is None:
                    department = Department(
                        id=self._ids.new_id(),
                        tenant_id=tenant.id,
                        code=BOOTSTRAP_DEPARTMENT_CODE,
                        name=BOOTSTRAP_DEPARTMENT_NAMES[tenant.default_locale.value],
                        created_at=now,
                        updated_at=now,
                    )
                    await uow.departments.add(department)
                    await self._audit.record(
                        uow,
                        principal,
                        "admin.department.created",
                        resource_type="department",
                        resource_id=department.id,
                        details={"code": department.code, "bootstrap": True},
                    )
                employee = Employee(
                    id=self._ids.new_id(),
                    tenant_id=tenant.id,
                    keycloak_sub=sub,
                    employee_number=cmd.employee_number,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    department_id=department.id,
                    hire_date=cmd.hire_date or now.date(),
                    locale=cmd.locale,
                    status=EmployeeStatus.ACTIVE,
                    created_at=now,
                    updated_at=now,
                    roles=BOOTSTRAP_ROLES,
                )
                await uow.employees.add(employee)
                await uow.employees.set_roles(employee.id, BOOTSTRAP_ROLES)
                await self._audit.record(
                    uow,
                    principal,
                    "admin.employee.created",
                    resource_type="employee",
                    resource_id=employee.id,
                    details={"bootstrap": True, "roles": sorted(r.value for r in BOOTSTRAP_ROLES)},
                )
                await uow.commit()
        except Exception:
            await self._identity.delete_user(
                sub
            )  # compensation: never leave an identity without an employee row
            raise
        return BootstrapResult(
            employee=replace(employee),
            department=department,
            username=email,
            temporary_password=temporary_password,
        )
