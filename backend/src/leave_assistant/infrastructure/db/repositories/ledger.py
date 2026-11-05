"""Append-only ledger repository and the balance view (ADR-0005)."""

from __future__ import annotations

import builtins
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from leave_assistant.application.ports import BalanceRow
from leave_assistant.domain.leave.ledger import EntryType, LedgerEntry
from leave_assistant.infrastructure.db.models import LeaveLedgerEntryRow
from leave_assistant.infrastructure.db.session import flush_or_conflict


class SqlLedgerRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add(self, entry: LedgerEntry) -> None:
        self._s.add(
            LeaveLedgerEntryRow(
                id=entry.id,
                tenant_id=entry.tenant_id,
                employee_id=entry.employee_id,
                leave_type_id=entry.leave_type_id,
                period_year=entry.period_year,
                entry_type=entry.entry_type.value,
                quantity_days=entry.quantity_days,
                effective_date=entry.effective_date,
                request_id=entry.request_id,
                reason=entry.reason,
                created_by_employee_id=entry.created_by_employee_id,
                idempotency_key=entry.idempotency_key,
                created_at=entry.created_at,
            )
        )
        await flush_or_conflict(self._s)

    async def exists(self, idempotency_key: str) -> bool:
        stmt = select(LeaveLedgerEntryRow.id).where(
            LeaveLedgerEntryRow.idempotency_key == idempotency_key,
            LeaveLedgerEntryRow.tenant_id == self._tenant,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none() is not None

    async def list(
        self, *, employee_id: UUID, leave_type_id: UUID | None = None, year: int | None = None
    ) -> builtins.list[LedgerEntry]:
        if self._tenant is None:
            return []
        stmt = select(LeaveLedgerEntryRow).where(
            LeaveLedgerEntryRow.tenant_id == self._tenant, LeaveLedgerEntryRow.employee_id == employee_id
        )
        if leave_type_id is not None:
            stmt = stmt.where(LeaveLedgerEntryRow.leave_type_id == leave_type_id)
        if year is not None:
            stmt = stmt.where(LeaveLedgerEntryRow.period_year == year)
        stmt = stmt.order_by(LeaveLedgerEntryRow.effective_date, LeaveLedgerEntryRow.created_at)
        return [
            LedgerEntry(
                id=r.id,
                tenant_id=r.tenant_id,
                employee_id=r.employee_id,
                leave_type_id=r.leave_type_id,
                period_year=r.period_year,
                entry_type=EntryType(r.entry_type),
                quantity_days=r.quantity_days,
                effective_date=r.effective_date,
                idempotency_key=r.idempotency_key,
                created_at=r.created_at,
                request_id=r.request_id,
                reason=r.reason,
                created_by_employee_id=r.created_by_employee_id,
            )
            for r in (await self._s.execute(stmt)).scalars()
        ]

    async def balances(self, *, year: int, employee_id: UUID | None = None) -> builtins.list[BalanceRow]:
        if self._tenant is None:
            return []
        sql = (
            "SELECT employee_id, leave_type_id, period_year, balance_days, pending_days FROM app.leave_balances "
            "WHERE tenant_id = :tenant_id AND period_year = :year"
            + (" AND employee_id = :employee_id" if employee_id else "")
            + " ORDER BY employee_id, leave_type_id"
        )
        params: dict[str, object] = {"tenant_id": self._tenant, "year": year}
        if employee_id:
            params["employee_id"] = employee_id
        rows = (await self._s.execute(text(sql), params)).all()
        return [BalanceRow(r[0], r[1], int(r[2]), Decimal(r[3]), Decimal(r[4])) for r in rows]


__all__ = ["SqlLedgerRepository", "datetime"]
