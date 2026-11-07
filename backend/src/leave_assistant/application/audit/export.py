"""Audit export as CSV (M10, doc 09 §2.8 "Export audited", brief §9 audit).

UTF-8 with a byte-order mark (spreadsheets open Greek text correctly), RFC 4180 quoting, `details` as compact JSON.
The export uses the list filters (from, to, action) and is capped at `MAX_ROWS`; a capped file says so in its last
line, so a narrower filter is needed for more. Every export is itself audited (`audit.exported`), with the filters
and the row count, never the contents.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, replace

from leave_assistant.application.audit.entry import AuditEntry
from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.ports import AuditFilter, UnitOfWorkFactory
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.tenancy import Principal

MAX_ROWS = 10_000
BATCH = 500
COLUMNS = (
    "occurred_at",
    "action",
    "outcome",
    "actor_kind",
    "actor_id",
    "actor_roles",
    "resource_type",
    "resource_id",
    "request_id",
    "thread_id",
    "details",
)


@dataclass(frozen=True, slots=True)
class AuditExport:
    content: bytes
    rows: int
    truncated: bool


def _row(e: AuditEntry) -> list[str]:
    return [
        e.occurred_at.isoformat(),
        e.action,
        e.outcome.value,
        e.actor_kind.value,
        e.actor_id,
        " ".join(e.actor_roles),
        e.resource_type,
        e.resource_id or "",
        e.request_id or "",
        str(e.thread_id) if e.thread_id else "",
        json.dumps(e.details, ensure_ascii=False, sort_keys=True, default=str) if e.details else "",
    ]


def render_csv(entries: list[AuditEntry], *, truncated: bool) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(_row(e) for e in entries)
    if truncated:
        writer.writerow([f"# truncated at {MAX_ROWS} rows: narrow the date range or the action filter"])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


async def export_audit(
    principal: Principal,
    uow_factory: UnitOfWorkFactory,
    audit: AuditService,
    *,
    filters: AuditFilter,
    platform: bool,
) -> AuditExport:
    require(principal, Action.READ_AUDIT_PLATFORM if platform else Action.READ_AUDIT_TENANT)
    if platform:
        filters = replace(filters, platform_only=True)
    context = TenantContext.for_principal(principal)
    entries: list[AuditEntry] = []
    after = None
    truncated = False
    async with uow_factory(context) as uow:
        while True:
            batch = await uow.audit.list(filters=filters, limit=BATCH, after=after)
            entries.extend(batch)
            if len(entries) > MAX_ROWS:
                entries, truncated = entries[:MAX_ROWS], True
                break
            if len(batch) < BATCH:
                break
            after = (batch[-1].occurred_at, batch[-1].id)
    async with uow_factory(context) as uow:
        await audit.record(
            uow,
            principal,
            "audit.exported",
            resource_type="audit_log",
            details={
                "scope": "platform" if platform else "tenant",
                "from": filters.occurred_from.isoformat() if filters.occurred_from else None,
                "to": filters.occurred_to.isoformat() if filters.occurred_to else None,
                "action": filters.action,
                "rows": len(entries),
                "truncated": truncated,
            },
        )
        await uow.commit()
    return AuditExport(render_csv(entries, truncated=truncated), len(entries), truncated)
