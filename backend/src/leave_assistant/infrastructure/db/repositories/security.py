"""PII vault, security defaults/policies and injection findings (M5, doc 07 §4.5)."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.ports import InjectionFinding, SecurityDefaults, SecurityPolicy, VaultEntry
from leave_assistant.domain.privacy.types import EntityType
from leave_assistant.infrastructure.db.models import (
    InjectionFindingRow,
    PiiVaultRow,
    SecurityPolicyDefaultsRow,
    SecurityPolicyRow,
)
from leave_assistant.infrastructure.db.session import flush_or_conflict

NEXT_SEQUENCE_SQL = text(
    """
    INSERT INTO app.pii_vault_counters (tenant_id, entity_type, next_seq) VALUES (:tenant_id, :entity_type, 2)
    ON CONFLICT (tenant_id, entity_type) DO UPDATE SET next_seq = app.pii_vault_counters.next_seq + 1
    RETURNING next_seq - 1
    """
)


def _vault_to_domain(r: PiiVaultRow) -> VaultEntry:
    return VaultEntry(
        id=r.id,
        tenant_id=r.tenant_id,
        entity_type=EntityType(r.entity_type),
        value_hmac=bytes(r.value_hmac),
        value_encrypted=bytes(r.value_encrypted),
        placeholder=r.placeholder,
        first_seen_at=r.first_seen_at,
        last_seen_at=r.last_seen_at,
        source_kinds=tuple(r.source_kinds or ()),
        subject_employee_id=r.subject_employee_id,
    )


class SqlVaultRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def get_by_hmac(self, value_hmac: bytes) -> VaultEntry | None:
        if self._tenant is None:
            return None
        stmt = select(PiiVaultRow).where(
            PiiVaultRow.tenant_id == self._tenant, PiiVaultRow.value_hmac == value_hmac
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _vault_to_domain(row) if row else None

    async def get_by_placeholder(self, placeholder: str) -> VaultEntry | None:
        if self._tenant is None:
            return None
        stmt = select(PiiVaultRow).where(
            PiiVaultRow.tenant_id == self._tenant, PiiVaultRow.placeholder == placeholder
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _vault_to_domain(row) if row else None

    async def next_sequence(self, entity_type: EntityType) -> int:
        if self._tenant is None:
            raise RuntimeError("vault sequences require a tenant context")
        result = await self._s.execute(
            NEXT_SEQUENCE_SQL, {"tenant_id": self._tenant, "entity_type": entity_type.value}
        )
        return int(result.scalar_one())

    async def add(self, entry: VaultEntry) -> None:
        self._s.add(
            PiiVaultRow(
                id=entry.id,
                tenant_id=entry.tenant_id,
                entity_type=entry.entity_type.value,
                value_hmac=entry.value_hmac,
                value_encrypted=entry.value_encrypted,
                placeholder=entry.placeholder,
                first_seen_at=entry.first_seen_at,
                last_seen_at=entry.last_seen_at,
                source_kinds=list(entry.source_kinds),
                subject_employee_id=entry.subject_employee_id,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, entry: VaultEntry) -> None:
        row = await self._s.get(PiiVaultRow, entry.id)
        if row is None or row.tenant_id != self._tenant:
            return
        row.last_seen_at = entry.last_seen_at
        row.source_kinds = list(entry.source_kinds)
        row.subject_employee_id = entry.subject_employee_id
        await self._s.flush()

    async def count(self) -> int:
        if self._tenant is None:
            return 0
        stmt = select(func.count()).select_from(PiiVaultRow).where(PiiVaultRow.tenant_id == self._tenant)
        return int((await self._s.execute(stmt)).scalar_one())


def _defaults_to_domain(r: SecurityPolicyDefaultsRow) -> SecurityDefaults:
    return SecurityDefaults(
        risk_low_max=float(r.risk_low_max),
        risk_medium_max=float(r.risk_medium_max),
        injection_llm_judge=bool(r.injection_llm_judge),
        user_input_block_threshold=float(r.user_input_block_threshold),
        max_upload_mb=int(r.max_upload_mb),
        allowed_mime=tuple(r.allowed_mime or ()),
        threshold_min=float(r.threshold_min),
        threshold_max=float(r.threshold_max),
        max_upload_mb_limit=int(r.max_upload_mb_limit),
        updated_by=r.updated_by,
    )


class SqlSecurityDefaultsRepository:
    """Single platform row (id = 1), readable by every tenant context; written by platform administrators."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self) -> SecurityDefaults:
        row = await self._s.get(SecurityPolicyDefaultsRow, 1)
        return _defaults_to_domain(row) if row else SecurityDefaults()

    async def update(self, defaults: SecurityDefaults) -> None:
        row = await self._s.get(SecurityPolicyDefaultsRow, 1)
        if row is None:
            row = SecurityPolicyDefaultsRow(id=1, updated_at=func.now())
            self._s.add(row)
        row.risk_low_max = defaults.risk_low_max
        row.risk_medium_max = defaults.risk_medium_max
        row.injection_llm_judge = defaults.injection_llm_judge
        row.user_input_block_threshold = defaults.user_input_block_threshold
        row.max_upload_mb = defaults.max_upload_mb
        row.allowed_mime = list(defaults.allowed_mime)
        row.threshold_min = defaults.threshold_min
        row.threshold_max = defaults.threshold_max
        row.max_upload_mb_limit = defaults.max_upload_mb_limit
        row.updated_by = defaults.updated_by
        row.updated_at = func.now()
        await self._s.flush()


def _policy_to_domain(r: SecurityPolicyRow) -> SecurityPolicy:
    return SecurityPolicy(
        tenant_id=r.tenant_id,
        risk_low_max=float(r.risk_low_max),
        risk_medium_max=float(r.risk_medium_max),
        injection_llm_judge=bool(r.injection_llm_judge),
        user_input_block_threshold=float(r.user_input_block_threshold),
        max_upload_mb=int(r.max_upload_mb),
        allowed_mime=tuple(r.allowed_mime or ()),
        updated_by=r.updated_by,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


class SqlSecurityPolicyRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def get(self) -> SecurityPolicy | None:
        if self._tenant is None:
            return None
        row = await self._s.get(SecurityPolicyRow, self._tenant)
        return _policy_to_domain(row) if row else None

    async def upsert(self, policy: SecurityPolicy) -> None:
        if policy.tenant_id != self._tenant:
            raise RuntimeError("security policy tenant does not match the unit of work")
        row = await self._s.get(SecurityPolicyRow, policy.tenant_id)
        if row is None:
            row = SecurityPolicyRow(tenant_id=policy.tenant_id, created_at=policy.created_at)
            self._s.add(row)
        row.risk_low_max = policy.risk_low_max
        row.risk_medium_max = policy.risk_medium_max
        row.injection_llm_judge = policy.injection_llm_judge
        row.user_input_block_threshold = policy.user_input_block_threshold
        row.max_upload_mb = policy.max_upload_mb
        row.allowed_mime = list(policy.allowed_mime)
        row.updated_by = policy.updated_by
        row.updated_at = policy.updated_at
        await flush_or_conflict(self._s)


def _finding_to_domain(r: InjectionFindingRow) -> InjectionFinding:
    return InjectionFinding(
        id=r.id,
        tenant_id=r.tenant_id,
        subject_type=r.subject_type,
        subject_id=r.subject_id,
        detector=r.detector,
        score=float(r.score),
        label=r.label,
        excerpt_hash=r.excerpt_hash,
        details=dict(r.details or {}),
        created_at=r.created_at,
    )


class SqlInjectionFindingRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add_many(self, findings: Sequence[InjectionFinding]) -> None:
        for finding in findings:
            self._s.add(
                InjectionFindingRow(
                    id=finding.id,
                    tenant_id=finding.tenant_id,
                    subject_type=finding.subject_type,
                    subject_id=finding.subject_id,
                    detector=finding.detector,
                    score=finding.score,
                    label=finding.label,
                    excerpt_hash=finding.excerpt_hash,
                    details=finding.details,
                    created_at=finding.created_at,
                )
            )
        if findings:
            await flush_or_conflict(self._s)

    async def list_for_subject(self, subject_type: str, subject_id: str) -> builtins.list[InjectionFinding]:
        if self._tenant is None:
            return []
        stmt = (
            select(InjectionFindingRow)
            .where(
                InjectionFindingRow.tenant_id == self._tenant,
                InjectionFindingRow.subject_type == subject_type,
                InjectionFindingRow.subject_id == subject_id,
            )
            .order_by(InjectionFindingRow.created_at, InjectionFindingRow.id)
        )
        return [_finding_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]
