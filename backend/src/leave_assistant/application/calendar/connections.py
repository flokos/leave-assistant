"""Calendar connections and the backend-driven OAuth flow (doc 10 §2, M8 decision 4).

`state` is a random nonce; only its SHA-256 is stored (`platform.oauth_states`, 10 minutes, single use, consumed
atomically like the M5 download tokens). The callback has no bearer token: the initiating principal comes from the
stored state and is re-checked against the current employee record (still active, still a company admin for the
company calendar). Refresh tokens are encrypted with `FileCipher` (AAD = connection id); access tokens never leave
the provider adapter.
"""

from __future__ import annotations

import builtins
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, ResourceRef, require
from leave_assistant.application.calendar.errors import (
    CalendarNotConnected,
    InsufficientScope,
)
from leave_assistant.application.calendar.models import (
    CalendarConnection,
    CalendarProviderError,
    CalendarTarget,
    OAuthState,
)
from leave_assistant.application.calendar.ports import FALLBACK_SCOPES, SCOPES, CalendarProvider, OAuthClient
from leave_assistant.application.calendar.sync import CalendarSyncService, connection_aad
from leave_assistant.application.ports import Clock, FileCipher, IdGenerator, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.calendar.model import ConnectionKind, ConnectionStatus
from leave_assistant.domain.tenancy import EmployeeStatus, Principal, Role

STATE_TTL = timedelta(minutes=10)


def _hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def _action(kind: ConnectionKind) -> Action:
    return (
        Action.MANAGE_CALENDAR_COMPANY
        if kind is ConnectionKind.COMPANY_SHARED
        else Action.CONNECT_PERSONAL_CALENDAR
    )


@dataclass(frozen=True, slots=True)
class CallbackResult:
    status: str  # connected | error
    reason: str = ""
    kind: ConnectionKind | None = None
    connection_id: UUID | None = None


class CalendarConnectionService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        oauth: OAuthClient,
        provider: CalendarProvider,
        cipher: FileCipher,
        sync: CalendarSyncService,
        *,
        redirect_uri: str,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._oauth = oauth
        self._provider = provider
        self._cipher = cipher
        self._sync = sync
        self.redirect_uri = redirect_uri

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def list(self, principal: Principal) -> builtins.list[CalendarConnection]:
        """The caller's personal connection; company administrators also see the company connection."""
        require(principal, Action.CONNECT_PERSONAL_CALENDAR, self._own(principal))
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            found = [await uow.calendar_connections.find_live(ConnectionKind.PERSONAL, principal.employee_id)]
            if Role.COMPANY_ADMIN in principal.roles:
                found.insert(0, await uow.calendar_connections.find_live(ConnectionKind.COMPANY_SHARED, None))
        return [c for c in found if c is not None]

    @staticmethod
    def _own(principal: Principal) -> ResourceRef:
        return ResourceRef(
            "calendar_connection", tenant_id=principal.tenant_id, owner_employee_id=principal.employee_id
        )

    async def start(self, principal: Principal, kind: ConnectionKind) -> str:
        """→ the provider's consent URL; the nonce is returned to the browser only inside that URL."""
        require(principal, _action(kind), self._own(principal))
        assert principal.tenant_id is not None and principal.employee_id is not None
        nonce = secrets.token_urlsafe(32)
        now = self._clock.now()
        async with self._uow(None) as uow:
            await uow.oauth_states.purge(now - timedelta(days=1))
            await uow.oauth_states.add(
                OAuthState(
                    nonce_hash=_hash(nonce),
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    employee_id=principal.employee_id,
                    kind=kind,
                    provider=self._provider.name,
                    roles=tuple(sorted(r.value for r in principal.roles)),
                    expires_at=now + STATE_TTL,
                    created_at=now,
                )
            )
            await uow.commit()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            await self._audit.record(
                uow,
                principal,
                "calendar.connection.started",
                resource_type="calendar_connection",
                details={"kind": kind.value, "provider": self._provider.name},
            )
            await uow.commit()
        return self._oauth.authorization_url(state=nonce, redirect_uri=self.redirect_uri, scopes=SCOPES[kind])

    async def callback(self, *, state: str | None, code: str | None, error: str | None) -> CallbackResult:
        """Completes the consent; never raises — the browser is redirected to the SPA with a status."""
        if not state:
            return CallbackResult("error", "state_missing")
        now = self._clock.now()
        async with self._uow(None) as uow:
            stored = await uow.oauth_states.consume(_hash(state), now)
            await uow.commit()
        if stored is None:
            return CallbackResult("error", "state_invalid")
        if error or not code:
            return CallbackResult(
                "error", "consent_denied" if error == "access_denied" else "consent_failed", stored.kind
            )
        ctx = TenantContext(tenant_id=stored.tenant_id, user_id=stored.user_id, roles=stored.roles)
        async with self._uow(ctx) as uow:
            employee = await uow.employees.get(stored.employee_id)
        if employee is None or employee.status is not EmployeeStatus.ACTIVE:
            return CallbackResult("error", "principal_changed", stored.kind)
        if stored.kind is ConnectionKind.COMPANY_SHARED and Role.COMPANY_ADMIN not in employee.roles:
            return CallbackResult("error", "principal_changed", stored.kind)
        try:
            grant = await self._oauth.exchange(
                code=code, state=state, redirect_uri=self.redirect_uri, scopes=SCOPES[stored.kind]
            )
        except CalendarProviderError:
            return CallbackResult("error", "exchange_failed", stored.kind)
        granted = set(grant.scopes)
        if not (set(SCOPES[stored.kind]) <= granted or set(FALLBACK_SCOPES[stored.kind]) <= granted):
            await self._oauth.revoke(grant.refresh_token)
            return CallbackResult("error", "insufficient_scope", stored.kind)
        try:
            connection = await self._store(stored, ctx, grant.refresh_token, tuple(sorted(granted)))
        except CalendarProviderError:
            return CallbackResult("error", "calendar_setup_failed", stored.kind)
        return CallbackResult("connected", "", stored.kind, connection.id)

    async def _store(
        self, stored: OAuthState, ctx: TenantContext, refresh_token: str, scopes: tuple[str, ...]
    ) -> CalendarConnection:
        now = self._clock.now()
        employee_id = stored.employee_id if stored.kind is ConnectionKind.PERSONAL else None
        async with self._uow(ctx) as uow:
            existing = await uow.calendar_connections.find_live(stored.kind, employee_id)
            connection = existing or CalendarConnection(
                id=self._ids.new_id(),
                tenant_id=stored.tenant_id,
                kind=stored.kind,
                employee_id=employee_id,
                provider=stored.provider,
                status=ConnectionStatus.ACTIVE,
                authorised_by_employee_id=stored.employee_id,
                created_at=now,
                updated_at=now,
                external_calendar_id=None if stored.kind is ConnectionKind.COMPANY_SHARED else "primary",
            )
            tenant = await uow.tenants.get(stored.tenant_id)
        ciphertext, dek = self._cipher.encrypt(refresh_token.encode(), aad=connection_aad(connection.id))
        connection.refresh_token_encrypted, connection.refresh_token_dek = ciphertext, dek
        connection.scopes = scopes
        if connection.kind is ConnectionKind.COMPANY_SHARED and not connection.external_calendar_id:
            target = CalendarTarget(stored.tenant_id, connection.id, "", refresh_token)
            company = tenant.name if tenant else ""
            connection.external_calendar_id = await self._provider.create_calendar(
                target, f"Leave — {company}"
            )
        async with self._uow(ctx) as uow:
            connection.status = ConnectionStatus.ACTIVE
            connection.authorised_by_employee_id = stored.employee_id
            connection.last_error = None
            connection.updated_at = now
            if existing is None:
                await uow.calendar_connections.add(connection)
            else:
                await uow.calendar_connections.update(connection)
            resumed = await self._sync.resume_parked(uow, connection.id)
            await self._audit.record(
                uow,
                None,
                "calendar.connection.authorised",
                resource_type="calendar_connection",
                resource_id=connection.id,
                details={
                    "kind": connection.kind.value,
                    "provider": connection.provider,
                    "reauthorised": existing is not None,
                    "resumed_jobs": resumed,
                    "by_employee_id": str(stored.employee_id),
                },
            )
            await uow.commit()
        return connection

    async def revoke(self, principal: Principal, connection_id: UUID) -> None:
        """Revoke at the provider, delete the tokens, mark the app's events orphaned (not deleted: doc 10 §2)."""
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            connection = await uow.calendar_connections.get(connection_id)
            if connection is None or connection.status is ConnectionStatus.REVOKED:
                raise CalendarNotConnected("calendar connection not found")
            owner = connection.employee_id if connection.kind is ConnectionKind.PERSONAL else None
            if connection.kind is ConnectionKind.PERSONAL and owner != principal.employee_id:
                raise CalendarNotConnected(
                    "calendar connection not found"
                )  # someone else's: indistinguishable
            require(
                principal,
                _action(connection.kind),
                ResourceRef(
                    "calendar_connection", connection.id, principal.tenant_id, owner_employee_id=owner
                ),
            )
        token = self._sync.target(connection).refresh_token
        if token:
            try:
                await self._oauth.revoke(token)
            except CalendarProviderError:
                pass  # the provider may already consider it revoked; local deletion proceeds regardless
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            current = await uow.calendar_connections.get(connection_id)
            if current is None:
                raise CalendarNotConnected("calendar connection not found")
            current.status = ConnectionStatus.REVOKED
            current.refresh_token_encrypted = None
            current.refresh_token_dek = None
            current.updated_at = now
            await uow.calendar_connections.update(current)
            orphaned = await uow.calendar_events.mark_orphaned(connection_id, now)
            await self._audit.record(
                uow,
                principal,
                "calendar.connection.revoked",
                resource_type="calendar_connection",
                resource_id=connection_id,
                details={"kind": current.kind.value, "orphaned_events": orphaned},
            )
            await uow.commit()


__all__ = ["CalendarConnectionService", "CallbackResult", "InsufficientScope"]
