"""Documents against PostgreSQL: schema/indexes, Greek FTS, vector search under RLS, predicate parity, supersede,
SKIP LOCKED claiming (doc 11 §4 "Documents", doc 12 M4 exit criteria)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from leave_assistant.application.container import Services
from leave_assistant.application.documents.commands import CreateDocument, UpdateDocument, Upload
from leave_assistant.application.ports import DocumentFilter
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.types import AclPrincipalType, Category, VersionStatus
from leave_assistant.domain.documents.visibility import DocumentScope, Reader, can_read
from leave_assistant.domain.tenancy import Employee, Tenant
from leave_assistant.infrastructure.db.session import create_engine, make_session_factory
from leave_assistant.infrastructure.db.uow import SqlAlchemyUnitOfWorkFactory
from leave_assistant.seed.run import run_seed
from tests.conftest import fake_document_adapters, principal_for
from tests.integration.conftest import RLS_URL

pytestmark = pytest.mark.integration

FAQ = Path(__file__).resolve().parents[2] / "src/leave_assistant/seed/corpus/files/aegean/faq.md"


@pytest.fixture
async def seeded(db_services: Services) -> dict[str, object]:
    report = await run_seed(db_services, "pw")
    assert not report.unchanged
    async with db_services.uow_factory(
        TenantContext(tenant_id=None, user_id="seed", platform_admin=True)
    ) as uow:
        aegean = await uow.tenants.get_by_slug("aegean")
        brightwater = await uow.tenants.get_by_slug("brightwater")
    assert aegean and brightwater
    employees: dict[str, dict[str, Employee]] = {}
    for tenant in (aegean, brightwater):
        async with db_services.uow_factory(
            TenantContext(tenant_id=tenant.id, user_id="seed", roles=("company_admin",))
        ) as uow:
            employees[tenant.slug] = {e.employee_number: e for e in await uow.employees.list_active()}
    return {"aegean": aegean, "brightwater": brightwater, "employees": employees}


def upload_of(path: Path, mime: str) -> Upload:
    return Upload(path=path, filename=path.name, declared_mime=mime, size_bytes=path.stat().st_size)


async def test_schema_indexes_and_unaccent(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as conn:
        indexes = {
            row[0]: row[1]
            for row in (
                await conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'app' AND tablename = 'document_chunks'"
                    )
                )
            ).all()
        }
        assert (
            "USING hnsw" in indexes["ix_document_chunks_embedding"]
            and "WHERE retrievable" in indexes["ix_document_chunks_embedding"]
        )
        assert all("USING gin" in indexes[f"ix_document_chunks_tsv_{c}"] for c in ("el", "en", "simple"))
        unaccented = (await conn.execute(text("SELECT app.f_unaccent('Κανονική άδεια')"))).scalar_one()
        assert unaccented == "Κανονικη αδεια"
        stem = (await conn.execute(text("SELECT to_tsvector('greek', 'οι εργαζόμενοι')"))).scalar_one()
        assert "εργαζομεν" in str(stem)
        jobs_rls = (
            await conn.execute(
                text("SELECT relrowsecurity FROM pg_class WHERE oid = 'platform.jobs'::regclass")
            )
        ).scalar_one()
        assert jobs_rls is False
        doc_rls = (
            await conn.execute(
                text("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'app.document_chunks'::regclass")
            )
        ).scalar_one()
        assert doc_rls is True


async def test_seed_uploads_corpus_and_processing_indexes_markdown_under_rls(
    db_services: Services, seeded: dict[str, object], engine: AsyncEngine
) -> None:
    aegean: Tenant = seeded["aegean"]  # type: ignore[assignment]
    employees = seeded["employees"]["aegean"]  # type: ignore[index]
    hr = principal_for(employees["AEG-002"])
    employee = principal_for(employees["AEG-004"])
    page = await db_services.documents.list(hr, filters=DocumentFilter(), limit=None, cursor=None)
    assert len(page.items) == 6 and all(
        s.latest_version and s.latest_version.status is VersionStatus.UPLOADED for s in page.items
    )
    jobs = await db_services.jobs.counts()
    assert jobs == {"queued": 12}  # 6 Aegean + 5 Brightwater + the superseded handbook version
    faq = next(s for s in page.items if s.document.title == "Συχνές ερωτήσεις / FAQ")
    assert faq.latest_version is not None
    outcome = await db_services.ingestion.process(aegean.id, faq.latest_version.id)
    assert outcome.status is VersionStatus.ACTIVE and outcome.chunks >= 2
    bundle = await db_services.retrieval.search(employee, "Πόσες ημέρες κανονικής άδειας δικαιούμαι;")
    assert bundle.hits and bundle.hits[0].document.id == faq.document.id and bundle.language == "el"
    assert all(h.chunk.embedding is None or len(h.chunk.embedding) == 384 for h in bundle.hits)
    # keyword leg alone: Greek stemming (εργαζόμενοι vs εργαζομένων) and accent folding (αδεια)
    async with db_services.uow_factory(TenantContext.for_principal(employee)) as uow:
        reader = Reader(employee.roles, employee.employee_id, employee.department_id)
        stemmed = await uow.chunks.keyword_search(
            "ημέρες κανονικής άδειας", language="el", reader=reader, limit=10
        )
        assert stemmed
        unaccented = await uow.chunks.keyword_search("αδεια", language="el", reader=reader, limit=10)
        assert unaccented
        english = await uow.chunks.keyword_search(
            "carry over unused days", language="en", reader=reader, limit=10
        )
        assert english
    # the other tenant sees nothing — through the service and through raw SQL as app_rls
    stranger = principal_for(seeded["employees"]["brightwater"]["BWS-004"])  # type: ignore[index]
    assert not (await db_services.retrieval.search(stranger, "κανονικής άδειας")).hits
    factory = make_session_factory(engine)
    async with factory() as session:
        session.info["tenant_context"] = TenantContext(tenant_id=seeded["brightwater"].id, user_id="x")  # type: ignore[attr-defined]
        count = (await session.execute(text("SELECT count(*) FROM app.document_chunks"))).scalar_one()
        assert count == 0
    async with factory() as session:
        session.info["tenant_context"] = TenantContext(tenant_id=aegean.id, user_id="x")
        count = (await session.execute(text("SELECT count(*) FROM app.document_chunks"))).scalar_one()
        assert count == outcome.chunks


async def test_visibility_predicate_matches_the_python_rules(
    db_services: Services, seeded: dict[str, object], tmp_path: Path
) -> None:
    employees = seeded["employees"]["aegean"]  # type: ignore[index]
    hr, admin, manager = (principal_for(employees[n]) for n in ("AEG-002", "AEG-001", "AEG-003"))
    source = tmp_path / "n.md"
    source.write_text("# Notice\n\nOps notice.")
    ops_id = employees["AEG-003"].department_id
    notice = await db_services.documents.create(
        manager,
        CreateDocument(
            "Ops notice", Category.DEPARTMENT_NOTICE, upload_of(source, "text/markdown"), department_id=ops_id
        ),
    )
    assert notice.document.department_id == ops_id
    internal_src = tmp_path / "i.md"
    internal_src.write_text("# Internal\n\nConfig notes.")
    internal = await db_services.documents.create(
        admin, CreateDocument("Internal", Category.ADMIN_INTERNAL, upload_of(internal_src, "text/markdown"))
    )
    fin_id = employees["AEG-006"].department_id
    await db_services.documents.update(
        admin,
        internal.document.id,
        UpdateDocument(version=1, acl=[(AclPrincipalType.DEPARTMENT, str(fin_id))]),
    )
    principals = [principal_for(e) for e in employees.values()]
    for principal in principals:
        reader = Reader(principal.roles, principal.employee_id, principal.department_id)
        async with db_services.uow_factory(TenantContext.for_principal(principal)) as uow:
            via_sql = {
                d.id
                for d in await uow.documents.list(
                    reader=reader, filters=DocumentFilter(), limit=100, after=None
                )
            }
            all_docs = []
            for title in [
                "Ops notice",
                "Internal",
                "Κανονισμός Αδειών",
                "Οδηγός Εξαιρέσεων",
                "Αργίες 2026",
                "Συχνές ερωτήσεις / FAQ",
                "Leave Policy (English translation)",
                "Διαδικασία Αναρρωτικής Άδειας",
            ]:
                document = await uow.documents.get_by_title(title)
                assert document is not None, title
                department = (
                    await uow.departments.get(document.department_id) if document.department_id else None
                )
                acl = await uow.document_acl.list_for_document(document.id)
                all_docs.append(
                    (
                        document,
                        DocumentScope(
                            document.category,
                            document.owner_employee_id,
                            document.department_id,
                            department.manager_employee_id if department else None,
                            tuple(acl),
                        ),
                    )
                )
        via_python = {d.id for d, scope in all_docs if can_read(reader, scope)}
        assert via_sql == via_python, principal.username
    # sanity on the matrix itself: the FIN employee reads the internal document only through the ACL
    fin_employee = next(
        p
        for p in principals
        if p.department_id == fin_id and not p.has_any(*[r for r in p.roles if r.value != "employee"])
    )
    async with db_services.uow_factory(TenantContext.for_principal(fin_employee)) as uow:
        reader = Reader(fin_employee.roles, fin_employee.employee_id, fin_employee.department_id)
        visible = {
            d.title
            for d in await uow.documents.list(reader=reader, filters=DocumentFilter(), limit=100, after=None)
        }
    assert "Internal" in visible and "Ops notice" not in visible and "Οδηγός Εξαιρέσεων" not in visible


async def test_supersede_flags_and_retrievable_chunks(
    db_services: Services, seeded: dict[str, object], tmp_path: Path
) -> None:
    brightwater: Tenant = seeded["brightwater"]  # type: ignore[assignment]
    employees = seeded["employees"]["brightwater"]  # type: ignore[index]
    hr = principal_for(employees["BWS-002"])
    handbook = next(
        s
        for s in (
            await db_services.documents.list(hr, filters=DocumentFilter(), limit=None, cursor=None)
        ).items
        if s.document.title == "Employee Leave Handbook"
    )
    versions = await db_services.documents.versions(hr, handbook.document.id)
    assert [v.version_no for v in versions] == [1, 2]
    # the PDFs have no canned parse in the fake parser: use Markdown versions of the same document instead
    v3_src = tmp_path / "h3.md"
    v3_src.write_text("# Employee Leave Handbook\n\n## 2. Annual leave\n\nTwenty-five days of annual leave.")
    v3 = await db_services.documents.add_version(hr, handbook.document.id, upload_of(v3_src, "text/markdown"))
    v4_src = tmp_path / "h4.md"
    v4_src.write_text(
        "# Employee Leave Handbook\n\n## 2. Annual leave\n\nTwenty days of annual leave per year."
    )
    v4 = await db_services.documents.add_version(hr, handbook.document.id, upload_of(v4_src, "text/markdown"))
    await db_services.ingestion.process(brightwater.id, v3.id)
    await db_services.ingestion.process(brightwater.id, v4.id)
    detail = await db_services.documents.get(hr, handbook.document.id)
    statuses = {v.version_no: (v.status, v.retrievable) for v in detail.versions}
    assert statuses[3] == (VersionStatus.SUPERSEDED, False) and statuses[4] == (VersionStatus.ACTIVE, True)
    assert detail.document.active_version_id == v4.id
    async with db_services.uow_factory(TenantContext.for_principal(hr)) as uow:
        assert all(not c.retrievable for c in await uow.chunks.list_for_version(v3.id))
        assert all(c.retrievable for c in await uow.chunks.list_for_version(v4.id))
    employee = principal_for(employees["BWS-004"])
    bundle = await db_services.retrieval.search(employee, "annual leave days")
    assert bundle.hits and {h.version.id for h in bundle.hits} == {v4.id}
    assert "Twenty days" in bundle.hits[0].chunk.content


async def test_skip_locked_claims_are_exclusive(
    db_services: Services, seeded: dict[str, object], settings
) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine(settings, url=RLS_URL, pool_size=6, max_overflow=0)
    try:
        factory = SqlAlchemyUnitOfWorkFactory(make_session_factory(engine))
        services = db_services.__class__.build(
            uow_factory=factory,
            identity=db_services.identity,
            verifier=db_services.verifier,
            clock=db_services.clock,
            ids=db_services.ids,
            adapters=fake_document_adapters(dimension=384),
        )
        results = await asyncio.gather(*(services.jobs.claim(f"w{i}") for i in range(6)))
        claimed = [j for j in results if j is not None]
        assert len(claimed) == 6 and len({j.id for j in claimed}) == 6
        assert {j.status.value for j in claimed} == {"running"} and all(j.attempts == 1 for j in claimed)
        remaining = await services.jobs.counts()
        assert remaining == {"queued": 6, "running": 6}
    finally:
        await engine.dispose()
