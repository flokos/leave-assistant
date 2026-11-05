"""Tenant provisioning and bootstrap (FR-TEN-07, Q-12), including Keycloak compensation."""

from __future__ import annotations

from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import (
    AuthorizationError,
    ConflictError,
    DependencyError,
    NotFoundError,
)
from leave_assistant.application.tenancy.commands import BootstrapAdmin, CreateTenant, UpdateTenant
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.errors import InvalidValue
from leave_assistant.domain.tenancy import Locale, Principal, Role, TenantStatus
from tests.conftest import principal_for
from tests.fakes.identity import FakeIdentityAdmin
from tests.fakes.memory import MemoryStore, MemoryUnitOfWork, MemoryUnitOfWorkFactory
from tests.fakes.world import World

CMD = CreateTenant(slug="testco", name="Test Co", default_locale=Locale.EL, timezone="Europe/Athens")
BOOT = BootstrapAdmin(email="Admin@TestCo.example", first_name="Ada", last_name="Lovelace", locale=Locale.EL)


async def test_create_tenant_creates_group_row_and_audit(
    services: Services, platform_admin: Principal, store: MemoryStore, identity: FakeIdentityAdmin
) -> None:
    tenant = await services.provisioning.create_tenant(platform_admin, CMD)
    assert tenant.status == TenantStatus.ACTIVE and tenant.keycloak_group_id is not None
    assert "/tenants/testco" in identity.groups and identity.groups["/tenants/testco"][1] == tenant.id
    assert store.tenants[tenant.id].slug == "testco"
    (entry,) = store.audit
    assert (
        entry.action == "admin.tenant.created"
        and entry.tenant_id is None
        and entry.actor_id == platform_admin.user_id
    )
    assert entry.details["slug"] == "testco"


async def test_duplicate_slug_conflicts_and_idempotent_ensure(
    services: Services, platform_admin: Principal
) -> None:
    tenant = await services.provisioning.create_tenant(platform_admin, CMD)
    with pytest.raises(ConflictError):
        await services.provisioning.create_tenant(platform_admin, CMD)
    same, created = await services.provisioning.ensure_tenant(platform_admin, CMD, allow_existing=True)
    assert same.id == tenant.id and created is False


@pytest.mark.parametrize(
    "cmd",
    [
        CreateTenant(slug="Bad Slug", name="x", default_locale=Locale.EN, timezone="Europe/Dublin"),
        CreateTenant(slug="ok-slug", name="x", default_locale=Locale.EN, timezone="Mars/Olympus"),
        CreateTenant(
            slug="ok-slug",
            name="x",
            default_locale=Locale.EN,
            timezone="Europe/Dublin",
            settings={"work_week": [9]},
        ),
        CreateTenant(
            slug="ok-slug", name="x", default_locale=Locale.EN, timezone="Europe/Dublin", settings={"nope": 1}
        ),
        CreateTenant(slug="ok-slug", name="   ", default_locale=Locale.EN, timezone="Europe/Dublin"),
    ],
)
async def test_invalid_commands(services: Services, platform_admin: Principal, cmd: CreateTenant) -> None:
    with pytest.raises(InvalidValue):
        await services.provisioning.create_tenant(platform_admin, cmd)


async def test_only_platform_admins_manage_tenants(services: Services, world: World) -> None:
    admin = principal_for(world.a.employees["company_admin"])
    with pytest.raises(AuthorizationError):
        await services.provisioning.create_tenant(admin, CMD)
    with pytest.raises(AuthorizationError):
        await services.provisioning.list_tenants(admin, limit=None, cursor=None)


async def test_update_and_status_are_audited(
    services: Services, platform_admin: Principal, store: MemoryStore
) -> None:
    tenant = await services.provisioning.create_tenant(platform_admin, CMD)
    updated = await services.provisioning.update_tenant(
        platform_admin, tenant.id, UpdateTenant(name="Renamed", settings={"max_upload_mb": 20})
    )
    assert (
        updated.name == "Renamed"
        and updated.settings.max_upload_mb == 20
        and updated.settings.allow_hr_fallback
    )
    suspended = await services.provisioning.set_status(platform_admin, tenant.id, TenantStatus.SUSPENDED)
    assert suspended.status == TenantStatus.SUSPENDED
    assert [e.action for e in store.audit] == [
        "admin.tenant.created",
        "admin.tenant.config.changed",
        "admin.tenant.config.changed",
    ]
    with pytest.raises(NotFoundError):
        await services.provisioning.get_tenant(platform_admin, UUID(int=999))


async def test_list_tenants_paginates(services: Services, platform_admin: Principal, world: World) -> None:
    await services.provisioning.create_tenant(platform_admin, CMD)
    page1 = await services.provisioning.list_tenants(platform_admin, limit=2, cursor=None)
    assert len(page1.items) == 2 and page1.next_cursor
    page2 = await services.provisioning.list_tenants(platform_admin, limit=2, cursor=page1.next_cursor)
    assert [t.slug for t in page2.items] == ["testco"] and page2.next_cursor is None


async def test_bootstrap_admin_creates_identity_department_employee(
    services: Services, platform_admin: Principal, store: MemoryStore, identity: FakeIdentityAdmin
) -> None:
    tenant = await services.provisioning.create_tenant(platform_admin, CMD)
    result = await services.provisioning.bootstrap_admin(platform_admin, tenant.id, BOOT)
    assert result.username == "admin@testco.example" and len(result.temporary_password) == 16
    user = identity.users[result.employee.keycloak_sub]
    assert (
        user.temporary
        and user.required_actions == ("UPDATE_PASSWORD",)
        and user.password == result.temporary_password
    )
    assert user.realm_roles == {"company_admin", "employee"} and user.group_paths == {"/tenants/testco"}
    assert result.department.code == "ADMIN" and result.department.name == "Διοίκηση"
    assert store.employees[result.employee.id].roles == {Role.COMPANY_ADMIN, Role.EMPLOYEE}
    actions = [(e.action, e.tenant_id) for e in store.audit]
    assert actions == [
        ("admin.tenant.created", None),
        ("admin.department.created", tenant.id),
        ("admin.employee.created", tenant.id),
    ]
    assert store.audit[-1].details == {"bootstrap": True, "roles": ["company_admin", "employee"]}
    assert "temporary_password" not in str(store.audit[-1].details)


async def test_bootstrap_rejects_duplicates_and_suspended(
    services: Services, platform_admin: Principal, identity: FakeIdentityAdmin
) -> None:
    tenant = await services.provisioning.create_tenant(platform_admin, CMD)
    await services.provisioning.bootstrap_admin(platform_admin, tenant.id, BOOT)
    with pytest.raises(ConflictError):
        await services.provisioning.bootstrap_admin(platform_admin, tenant.id, BOOT)
    assert len(identity.users) == 1
    await services.provisioning.set_status(platform_admin, tenant.id, TenantStatus.SUSPENDED)
    with pytest.raises(ConflictError, match="suspended"):
        await services.provisioning.bootstrap_admin(
            platform_admin, tenant.id, BootstrapAdmin("other@testco.example", "O", "T", Locale.EN)
        )


class _FailingCommitFactory:
    """Fails the commit of the first tenant-scoped unit of work (simulates a database error after Keycloak succeeded)."""

    def __init__(self, inner: MemoryUnitOfWorkFactory, tenant_id: UUID) -> None:
        self._inner = inner
        self._tenant_id = tenant_id

    def __call__(self, context: TenantContext | None) -> MemoryUnitOfWork:
        uow = self._inner(context)
        if context is not None and context.tenant_id == self._tenant_id:
            return _FailingCommitUow(uow)
        return uow


class _FailingCommitUow:
    def __init__(self, inner: MemoryUnitOfWork) -> None:
        self._inner = inner
        self.context = inner.context

    async def __aenter__(self) -> Self:
        await self._inner.__aenter__()
        self.tenants, self.departments, self.employees = (
            self._inner.tenants,
            self._inner.departments,
            self._inner.employees,
        )
        self.audit, self.idempotency = self._inner.audit, self._inner.idempotency
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self._inner.__aexit__(exc_type, exc, tb)

    async def commit(self) -> None:
        raise RuntimeError("simulated database failure")

    async def rollback(self) -> None:
        await self._inner.rollback()


async def test_bootstrap_compensates_identity_when_database_fails(
    services: Services,
    platform_admin: Principal,
    store: MemoryStore,
    identity: FakeIdentityAdmin,
    uow_factory: MemoryUnitOfWorkFactory,
) -> None:
    tenant = await services.provisioning.create_tenant(platform_admin, CMD)
    failing = _FailingCommitFactory(uow_factory, tenant.id)
    provisioning = type(services.provisioning)(
        failing, identity, services.ids, services.clock, services.audit
    )
    with pytest.raises(RuntimeError, match="simulated"):
        await provisioning.bootstrap_admin(platform_admin, tenant.id, BOOT)
    assert identity.users == {}  # the Keycloak user was deleted again
    assert ("delete_user",) == identity.calls[-1][:1]
    assert not store.employees and not store.departments


async def test_identity_failure_leaves_no_rows(
    services: Services, platform_admin: Principal, store: MemoryStore, identity: FakeIdentityAdmin
) -> None:
    identity.fail_on.add("ensure_tenant_group")
    with pytest.raises(DependencyError):
        await services.provisioning.create_tenant(platform_admin, CMD)
    assert not store.tenants and not store.audit
