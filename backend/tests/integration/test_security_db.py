"""M5 tables against PostgreSQL: RLS on the vault/supporting/retention tables, atomic vault sequences, single-use
download tokens, platform defaults row + per-tenant back-fill, retention scan and deletion with real rows."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from leave_assistant.application.container import Services
from leave_assistant.application.documents.commands import Upload
from leave_assistant.application.errors import AuthorizationError, NotFoundError
from leave_assistant.application.ports import SecurityDefaults
from leave_assistant.application.retention.service import CreateRetentionPolicy
from leave_assistant.application.security.policy import UpdateSecurityPolicy
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.privacy.types import EntityType
from leave_assistant.domain.retention.eligibility import DocumentClass, RetentionAction, ReviewStatus
from leave_assistant.domain.tenancy import Employee, Tenant
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from tests.conftest import principal_for

pytestmark = pytest.mark.integration

NOTE = Path(__file__).resolve().parents[2] / "src/leave_assistant/seed/corpus/supporting/medical-note.png"
ADVERSARIAL_EL = (
    Path(__file__).resolve().parents[2]
    / "src/leave_assistant/seed/corpus/files/aegean/adversarial-simeiosi-el.md"
)
M5_TENANT_TABLES = {
    "pii_vault",
    "pii_vault_counters",
    "security_policies",
    "injection_findings",
    "supporting_documents",
    "supporting_document_access_events",
    "retention_policies",
    "retention_review_items",
}


async def _as_tenant(conn: AsyncConnection, tenant: Tenant) -> None:
    """`app_owner` is subject to forced RLS too, so raw checks need the tenant setting."""
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant.id)})


async def test_m5_tables_have_forced_rls_and_platform_rows(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'app' AND c.relkind = 'r'"
                )
            )
        ).all()
        flags = {name: (rls, forced) for name, rls, forced in rows}
        assert M5_TENANT_TABLES <= set(flags)
        assert all(flags[name] == (True, True) for name in M5_TENANT_TABLES)
        defaults = (
            await conn.execute(
                text("SELECT id, risk_low_max, risk_medium_max FROM platform.security_policy_defaults")
            )
        ).all()
        assert defaults == [(1, 0.3, 0.7)]
        platform_tables = {
            r[0]
            for r in (
                await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'platform'"))
            ).all()
        }
        assert {"supporting_download_tokens", "security_policy_defaults"} <= platform_tables


async def test_seed_backfills_policies_and_vault_isolation(
    db_services: Services,
    seeded: dict[str, object],
    sql_uow_factory: SqlAlchemyUnitOfWorkFactory,  # noqa: F811
) -> None:
    aegean: Tenant = seeded["aegean"]  # type: ignore[assignment]
    brightwater: Tenant = seeded["brightwater"]  # type: ignore[assignment]
    for tenant in (aegean, brightwater):
        async with sql_uow_factory(
            TenantContext(tenant_id=tenant.id, user_id="t", roles=("company_admin",))
        ) as uow:
            policy = await uow.security_policies.get()
            assert policy is not None and policy.tenant_id == tenant.id and policy.risk_low_max == 0.3
            assert {p.document_class for p in await uow.retention_policies.list()} == {
                DocumentClass.SUPPORTING_DOCUMENT,
                DocumentClass.COMPANY_DOCUMENT,
            }
            seq = [await uow.pii_vault.next_sequence(EntityType.PERSON) for _ in range(3)]
            assert seq == [1, 2, 3]
            await uow.commit()
    # a tenant context sees only its own counters/policies; no context sees nothing
    async with sql_uow_factory(
        TenantContext(tenant_id=aegean.id, user_id="t", roles=("company_admin",))
    ) as uow:
        assert await uow.pii_vault.next_sequence(EntityType.PERSON) == 4
        await uow.commit()
    async with sql_uow_factory(
        TenantContext(tenant_id=brightwater.id, user_id="t", roles=("company_admin",))
    ) as uow:
        assert await uow.pii_vault.next_sequence(EntityType.PERSON) == 4
        await uow.commit()
    async with sql_uow_factory(None) as uow:
        assert await uow.security_policies.get() is None and await uow.retention_policies.list() == []
        assert await uow.security_defaults.get() == SecurityDefaults(updated_by="migration")


async def test_supporting_document_round_trip_tokens_and_retention(
    db_services: Services, seeded: dict[str, object], owner_engine: AsyncEngine
) -> None:
    aegean: Tenant = seeded["aegean"]  # type: ignore[assignment]
    employees: dict[str, Employee] = seeded["employees"]["aegean"]  # type: ignore[index]
    employee = principal_for(employees["AEG-004"])
    hr = principal_for(employees["AEG-002"])
    manager = principal_for(employees["AEG-003"])
    # the seed attached the fictional medical note to Eleni's sick request
    async with db_services.uow_factory(TenantContext.for_principal(employee)) as uow:
        requests = await uow.leave_requests.list_for_employee(employee.employee_id)  # type: ignore[arg-type]
        sick = next(r for r in requests if r.start_date == date(2026, 11, 2))
    attached = await db_services.supporting.list_for_request(employee, sick.id)
    assert [d.original_filename for d in attached] == ["medical-note.png"]
    document = attached[0]
    async with owner_engine.connect() as conn:
        await _as_tenant(conn, aegean)
        dek = (
            await conn.execute(
                text("SELECT encrypted_dek FROM app.supporting_documents WHERE id = :id"), {"id": document.id}
            )
        ).scalar_one()
        assert len(bytes(dek)) == 40
    stored = db_services.adapters.storage.objects[document.object_key][0]  # type: ignore[attr-defined]
    assert stored[:8] != NOTE.read_bytes()[:8]
    # tokens: HR with reason → one download; the hash is consumed atomically
    issued = await db_services.supporting.issue_download_token(hr, document.id, "integration check")
    first, second = await asyncio.gather(
        db_services.supporting.download(issued.token),
        db_services.supporting.download(issued.token),
        return_exceptions=True,
    )
    outcomes = [isinstance(r, tuple) for r in (first, second)]
    assert outcomes.count(True) == 1, outcomes
    plaintext = next(r for r in (first, second) if isinstance(r, tuple))[1]
    assert plaintext == NOTE.read_bytes()
    with pytest.raises(NotFoundError):
        await db_services.supporting.get(manager, document.id)
    detail = await db_services.supporting.get(hr, document.id)
    assert {e.action for e in detail.access_events} >= {"download_token", "download", "view"}
    # retention: nothing eligible today; 400 days later the supporting document is listed, HR approves, job deletes
    admin = principal_for(employees["AEG-001"])
    report = await db_services.retention.scan_tenant(aegean.id)
    assert report.created == 0
    db_services.clock.current += timedelta(days=400)  # type: ignore[attr-defined]
    report = await db_services.retention.scan_tenant(aegean.id)
    assert report.created >= 1
    items = await db_services.retention.list_items(hr, status=ReviewStatus.PENDING)
    item = next(i for i in items if i.subject_id == document.id)
    decided = await db_services.retention.decide(hr, item.id, ReviewStatus.APPROVED_DELETE, "expired")
    assert decided.status is ReviewStatus.APPROVED_DELETE
    done = await db_services.retention.execute_deletion(aegean.id, item.id)
    assert done.status is ReviewStatus.DELETED
    assert await db_services.supporting.list_for_request(hr, sick.id) == []
    async with owner_engine.connect() as conn:
        await _as_tenant(conn, aegean)
        status = (
            await conn.execute(
                text("SELECT status FROM app.supporting_documents WHERE id = :id"), {"id": document.id}
            )
        ).scalar_one()
        assert status == "deleted"
        actions = {
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "SELECT DISTINCT action FROM audit.audit_log WHERE action LIKE 'retention.%' OR action LIKE 'supporting.%'"
                    )
                )
            ).all()
        }
    assert {
        "retention.scan",
        "retention.decision",
        "retention.deleted",
        "supporting.download",
        "supporting.uploaded",
    } <= actions
    with pytest.raises(AuthorizationError):  # the admin may change policies but never read items
        await db_services.retention.list_items(admin, status=None)
    await db_services.retention.create_policy(
        admin, CreateRetentionPolicy(DocumentClass.CONVERSATION, 90, RetentionAction.NOTIFY_ONLY)
    )


async def test_processing_pseudonymises_and_policy_drives_the_tier(
    db_services: Services, seeded: dict[str, object], owner_engine: AsyncEngine, tmp_path: Path
) -> None:
    aegean: Tenant = seeded["aegean"]  # type: ignore[assignment]
    employees: dict[str, Employee] = seeded["employees"]["aegean"]  # type: ignore[index]
    hr = principal_for(employees["AEG-002"])
    admin = principal_for(employees["AEG-001"])
    # seeded adversarial pair: process both versions with the fake adapters (heuristics + keyword classifier)
    async with db_services.uow_factory(TenantContext.for_principal(hr)) as uow:
        en = await uow.documents.get_by_title("Leave approvals appendix (untrusted upload)")
        el = await uow.documents.get_by_title("Σημείωμα διαδικασίας (ύποπτο)")
        assert en and el
        en_versions = await uow.document_versions.list_for_document(en.id)
        el_versions = await uow.document_versions.list_for_document(el.id)
    en_outcome = await db_services.ingestion.process(aegean.id, en_versions[0].id)
    el_outcome = await db_services.ingestion.process(aegean.id, el_versions[0].id)
    assert en_outcome.status.value == "QUARANTINED" and el_outcome.status.value == "PENDING_REVIEW"
    async with owner_engine.connect() as conn:
        await _as_tenant(conn, aegean)
        findings = (
            await conn.execute(
                text("SELECT detector, label FROM app.injection_findings WHERE subject_id = :s"),
                {"s": str(en_versions[0].id)},
            )
        ).all()
        assert ("heuristic", "override") in [tuple(r) for r in findings]
    # a document with PII: placeholders in content_llm_safe, vault rows under the tenant
    path = tmp_path / "contacts.md"
    path.write_text(
        "# Επαφές\n\nΗ Ελένη Δημητρίου (τηλ. 6971234567, eleni.dimitriou@aegean-logistics.example) είναι υπεύθυνη.\n",
        encoding="utf-8",
    )
    from leave_assistant.application.documents.commands import CreateDocument
    from leave_assistant.domain.documents.types import Category

    created = await db_services.documents.create(
        hr,
        CreateDocument(
            title="Επαφές",
            category=Category.POLICY_HR_RESTRICTED,
            upload=Upload(
                path=path,
                filename="contacts.md",
                declared_mime="text/markdown",
                size_bytes=path.stat().st_size,
            ),
        ),
    )
    assert created.latest_version is not None
    await db_services.ingestion.process(aegean.id, created.latest_version.id)
    async with owner_engine.connect() as conn:
        await _as_tenant(conn, aegean)
        safe = (
            await conn.execute(
                text(
                    "SELECT string_agg(content_llm_safe, ' ') FROM app.document_chunks WHERE version_id = :v"
                ),
                {"v": created.latest_version.id},
            )
        ).scalar_one()
        vault = (
            await conn.execute(text("SELECT placeholder, tenant_id FROM app.pii_vault ORDER BY placeholder"))
        ).all()
    assert (
        "6971234567" not in safe
        and "Δημητρίου" not in safe
        and "PERSON_0001" in safe
        and "PHONE_0001" in safe
    )
    assert {r[0] for r in vault} >= {"PERSON_0001", "PHONE_0001", "EMAIL_0001"} and {r[1] for r in vault} == {
        aegean.id
    }
    # the tenant policy changes the tier of the Greek footnote on a new version
    await db_services.security_policies.update_policy(
        admin, UpdateSecurityPolicy(risk_low_max=0.1, risk_medium_max=0.2)
    )
    source = ADVERSARIAL_EL
    v2_path = tmp_path / "v2.md"
    v2_path.write_text(source.read_text(encoding="utf-8") + "\n\nΈκδοση 2.\n", encoding="utf-8")  # noqa: ASYNC240
    v2 = await db_services.documents.add_version(
        hr,
        el.id,
        Upload(
            path=v2_path, filename="v2.md", declared_mime="text/markdown", size_bytes=v2_path.stat().st_size
        ),
    )
    assert (await db_services.ingestion.process(aegean.id, v2.id)).status.value == "QUARANTINED"
