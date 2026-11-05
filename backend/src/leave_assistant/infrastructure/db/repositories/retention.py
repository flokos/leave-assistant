"""Retention policies and review items (M5, doc 07 §7)."""

from __future__ import annotations

import builtins
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.ports import RetentionPolicy, RetentionReviewItem
from leave_assistant.domain.retention.eligibility import DocumentClass, RetentionAction, ReviewStatus
from leave_assistant.infrastructure.db.models import RetentionPolicyRow, RetentionReviewItemRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


def _policy_to_domain(r: RetentionPolicyRow) -> RetentionPolicy:
    return RetentionPolicy(
        id=r.id,
        tenant_id=r.tenant_id,
        document_class=DocumentClass(r.document_class),
        retention_days=int(r.retention_days),
        action=RetentionAction(r.action),
        enabled=bool(r.enabled),
        updated_by=r.updated_by,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


class SqlRetentionPolicyRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def list(self) -> builtins.list[RetentionPolicy]:
        if self._tenant is None:
            return []
        stmt = (
            select(RetentionPolicyRow)
            .where(RetentionPolicyRow.tenant_id == self._tenant)
            .order_by(RetentionPolicyRow.document_class)
        )
        return [_policy_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def get(self, policy_id: UUID) -> RetentionPolicy | None:
        row = await self._s.get(RetentionPolicyRow, policy_id)
        return _policy_to_domain(row) if row and row.tenant_id == self._tenant else None

    async def get_by_class(self, document_class: DocumentClass) -> RetentionPolicy | None:
        if self._tenant is None:
            return None
        stmt = select(RetentionPolicyRow).where(
            RetentionPolicyRow.tenant_id == self._tenant,
            RetentionPolicyRow.document_class == document_class.value,
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _policy_to_domain(row) if row else None

    async def add(self, policy: RetentionPolicy) -> None:
        self._s.add(
            RetentionPolicyRow(
                id=policy.id,
                tenant_id=policy.tenant_id,
                document_class=policy.document_class.value,
                retention_days=policy.retention_days,
                action=policy.action.value,
                enabled=policy.enabled,
                updated_by=policy.updated_by,
                created_at=policy.created_at,
                updated_at=policy.updated_at,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, policy: RetentionPolicy) -> None:
        row = await self._s.get(RetentionPolicyRow, policy.id)
        if row is None or row.tenant_id != self._tenant:
            return
        row.retention_days = policy.retention_days
        row.action = policy.action.value
        row.enabled = policy.enabled
        row.updated_by = policy.updated_by
        row.updated_at = policy.updated_at
        await self._s.flush()

    async def delete(self, policy_id: UUID) -> None:
        if self._tenant is None:
            return
        await self._s.execute(
            delete(RetentionPolicyRow).where(
                RetentionPolicyRow.id == policy_id, RetentionPolicyRow.tenant_id == self._tenant
            )
        )
        await flush_or_conflict(self._s)


def _item_to_domain(r: RetentionReviewItemRow) -> RetentionReviewItem:
    return RetentionReviewItem(
        id=r.id,
        tenant_id=r.tenant_id,
        policy_id=r.policy_id,
        subject_type=r.subject_type,
        subject_id=r.subject_id,
        label=r.label,
        eligible_since=r.eligible_since,
        status=ReviewStatus(r.status),
        created_at=r.created_at,
        reviewer_employee_id=r.reviewer_employee_id,
        decided_at=r.decided_at,
        comment=r.comment,
    )


class SqlRetentionReviewItemRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def list(self, *, status: ReviewStatus | None) -> builtins.list[RetentionReviewItem]:
        if self._tenant is None:
            return []
        stmt = select(RetentionReviewItemRow).where(RetentionReviewItemRow.tenant_id == self._tenant)
        if status is not None:
            stmt = stmt.where(RetentionReviewItemRow.status == status.value)
        stmt = stmt.order_by(RetentionReviewItemRow.created_at, RetentionReviewItemRow.id)
        return [_item_to_domain(r) for r in (await self._s.execute(stmt)).scalars()]

    async def get(self, item_id: UUID) -> RetentionReviewItem | None:
        row = await self._s.get(RetentionReviewItemRow, item_id)
        return _item_to_domain(row) if row and row.tenant_id == self._tenant else None

    async def latest_for(self, subject_type: str, subject_id: UUID) -> RetentionReviewItem | None:
        if self._tenant is None:
            return None
        stmt = (
            select(RetentionReviewItemRow)
            .where(
                RetentionReviewItemRow.tenant_id == self._tenant,
                RetentionReviewItemRow.subject_type == subject_type,
                RetentionReviewItemRow.subject_id == subject_id,
            )
            .order_by(RetentionReviewItemRow.created_at.desc(), RetentionReviewItemRow.id.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return _item_to_domain(row) if row else None

    async def add(self, item: RetentionReviewItem) -> None:
        self._s.add(
            RetentionReviewItemRow(
                id=item.id,
                tenant_id=item.tenant_id,
                policy_id=item.policy_id,
                subject_type=item.subject_type,
                subject_id=item.subject_id,
                label=item.label,
                eligible_since=item.eligible_since,
                status=item.status.value,
                created_at=item.created_at,
                reviewer_employee_id=item.reviewer_employee_id,
                decided_at=item.decided_at,
                comment=item.comment,
            )
        )
        await flush_or_conflict(self._s)

    async def update(self, item: RetentionReviewItem) -> None:
        row = await self._s.get(RetentionReviewItemRow, item.id)
        if row is None or row.tenant_id != self._tenant:
            return
        row.status = item.status.value
        row.reviewer_employee_id = item.reviewer_employee_id
        row.decided_at = item.decided_at
        row.comment = item.comment
        await self._s.flush()
