"""In-memory IdentityAdmin: records calls, supports failure injection (compensation tests)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from uuid import UUID

from leave_assistant.application.errors import DependencyError
from leave_assistant.application.ports import IdentityUser, NewIdentityUser


@dataclass
class FakeUser:
    sub: str
    username: str
    email: str
    first_name: str
    last_name: str
    locale: str
    password: str
    temporary: bool
    enabled: bool = True
    realm_roles: set[str] = field(default_factory=set)
    group_paths: set[str] = field(default_factory=set)

    @property
    def required_actions(self) -> tuple[str, ...]:
        return ("UPDATE_PASSWORD",) if self.temporary else ()

    def to_identity(self) -> IdentityUser:
        return IdentityUser(
            sub=self.sub,
            username=self.username,
            enabled=self.enabled,
            required_actions=self.required_actions,
            realm_roles=frozenset(self.realm_roles),
            group_paths=tuple(sorted(self.group_paths)),
        )


class FakeIdentityAdmin:
    def __init__(self) -> None:
        self.users: dict[str, FakeUser] = {}
        self.groups: dict[str, tuple[str, UUID]] = {}  # path -> (group id, tenant id)
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail_on: set[str] = set()

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name, args))
        if name in self.fail_on:
            raise DependencyError(f"identity provider failure injected on {name}")

    async def ensure_tenant_group(self, slug: str, tenant_id: UUID) -> str:
        self._record("ensure_tenant_group", slug, tenant_id)
        path = f"/tenants/{slug}"
        if path in self.groups:
            group_id, existing_tenant = self.groups[path]
            if existing_tenant != tenant_id:
                raise DependencyError("tenant group attribute mismatch")
            return group_id
        group_id = f"group-{uuid.uuid4()}"
        self.groups[path] = (group_id, tenant_id)
        return group_id

    async def find_user_by_username(self, username: str) -> IdentityUser | None:
        self._record("find_user_by_username", username)
        for user in self.users.values():
            if user.username == username:
                return user.to_identity()
        return None

    async def get_user(self, sub: str) -> IdentityUser | None:
        self._record("get_user", sub)
        user = self.users.get(sub)
        return user.to_identity() if user else None

    async def create_user(self, spec: NewIdentityUser) -> str:
        self._record("create_user", spec.username)
        if any(u.username == spec.username for u in self.users.values()):
            raise DependencyError("user exists")
        sub = str(uuid.uuid4())
        self.users[sub] = FakeUser(
            sub=sub,
            username=spec.username,
            email=spec.email,
            first_name=spec.first_name,
            last_name=spec.last_name,
            locale=spec.locale,
            password=spec.password,
            temporary=spec.temporary_password,
            realm_roles=set(spec.realm_roles),
            group_paths={spec.group_path},
        )
        return sub

    async def update_user(self, sub: str, *, first_name: str, last_name: str, locale: str) -> None:
        self._record("update_user", sub)
        user = self.users[sub]
        user.first_name, user.last_name, user.locale = first_name, last_name, locale

    async def set_enabled(self, sub: str, enabled: bool) -> None:
        self._record("set_enabled", sub, enabled)
        self.users[sub].enabled = enabled

    async def set_realm_roles(self, sub: str, roles: frozenset[str], *, managed: frozenset[str]) -> None:
        self._record("set_realm_roles", sub, roles)
        user = self.users[sub]
        user.realm_roles = (user.realm_roles - managed) | set(roles)

    async def reset_password(self, sub: str, password: str, *, temporary: bool) -> None:
        self._record("reset_password", sub, temporary)
        user = self.users[sub]
        user.password, user.temporary = password, temporary

    async def delete_user(self, sub: str) -> None:
        self._record("delete_user", sub)
        self.users.pop(sub, None)
