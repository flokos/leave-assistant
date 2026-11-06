"""The `VectorIndex` port (ADR-0011, M9): pgvector delegation, the Chroma comparison index (in-process client) with
PostgreSQL-computed visibility, and the vector-only mode of the retrieval service."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.documents.policy import reader_for
from leave_assistant.application.retrieval.service import RetrievalService
from leave_assistant.application.retrieval.vector_index import PgVectorIndex
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.types import Category
from tests.conftest import principal_for
from tests.fakes.world import World
from tests.unit.documents.conftest import ENGLISH_HANDBOOK_MD, GREEK_POLICY_MD, HR_GUIDE_MD
from tests.unit.documents.test_retrieval import ingest

chromadb = pytest.importorskip("chromadb")


@pytest.fixture
async def corpus(services: Services, world: World, uploads: Path) -> dict[str, UUID]:
    hr = principal_for(world.a.employees["hr_admin"])
    other_hr = principal_for(world.b.employees["hr_admin"])
    return {
        "greek": await ingest(services, hr, uploads, "Κανονισμός Αδειών", GREEK_POLICY_MD),
        "handbook": await ingest(services, hr, uploads, "Employee Leave Handbook", ENGLISH_HANDBOOK_MD),
        "restricted": await ingest(
            services, hr, uploads, "Exceptional Leave Guidelines", HR_GUIDE_MD, Category.POLICY_HR_RESTRICTED
        ),
        "other_tenant": await ingest(services, other_hr, uploads, "Other handbook", ENGLISH_HANDBOOK_MD),
    }


def _service(services: Services, index: object, *, hybrid: bool = True) -> RetrievalService:
    return RetrievalService(
        services.uow_factory,
        services.adapters.embedder,
        services.adapters.language,
        vector_index=index,  # type: ignore[arg-type]
        hybrid=hybrid,
    )


async def test_chroma_returns_the_pgvector_ranking_under_the_same_visibility(
    services: Services, world: World, corpus: dict[str, UUID]
) -> None:
    from leave_assistant.infrastructure.retrieval.chroma import ChromaSync, ChromaVectorIndex, collection_name

    client = chromadb.EphemeralClient()
    sync = ChromaSync(client, services.uow_factory)
    synced = {key: await sync.sync_tenant(t.tenant.id) for key, t in (("a", world.a), ("b", world.b))}
    assert synced["a"] > 0 and synced["b"] > 0
    assert client.get_collection(collection_name(world.a.tenant.id)).count() == synced["a"]

    employee = principal_for(world.a.employees["employee"])
    hr = principal_for(world.a.employees["hr_admin"])
    pg, chroma = (
        _service(services, PgVectorIndex(), hybrid=False),
        _service(services, ChromaVectorIndex(client), hybrid=False),
    )
    for principal, query in ((employee, "μεταφορά υπολοίπου κανονικής άδειας"), (hr, "compassionate leave")):
        expected = [h.chunk.id for h in (await pg.search(principal, query)).hits]
        actual = [h.chunk.id for h in (await chroma.search(principal, query)).hits]
        assert actual == expected  # identical vectors, identical visibility → identical ranking
    employee_docs = {h.document.id for h in (await chroma.search(employee, "compassionate leave")).hits}
    assert corpus["restricted"] not in employee_docs and corpus["other_tenant"] not in employee_docs
    hr_docs = {h.document.id for h in (await chroma.search(hr, "compassionate leave")).hits}
    assert corpus["restricted"] in hr_docs

    # a retired version stays in the collection but is no longer allowed by PostgreSQL
    async with services.uow_factory(
        TenantContext(tenant_id=world.a.tenant.id, user_id="t", roles=("system",))
    ) as uow:
        document = await uow.documents.get(corpus["handbook"])
        assert document is not None and document.active_version_id is not None
        await uow.chunks.set_retrievable(document.active_version_id, False)
        version = await uow.document_versions.get(document.active_version_id)
        assert version is not None
        version.retrievable = False
        await uow.document_versions.update(version)
        allowed = await uow.chunks.visible_version_ids(reader=reader_for(employee))
        await uow.commit()
    assert document.active_version_id not in allowed
    after = {h.document.id for h in (await chroma.search(employee, "annual leave days")).hits}
    assert corpus["handbook"] not in after


async def test_vector_only_mode_skips_the_keyword_leg(
    services: Services, world: World, corpus: dict[str, UUID]
) -> None:
    employee = principal_for(world.a.employees["employee"])
    hybrid = await _service(services, PgVectorIndex()).search(employee, "annual leave days")
    vector = await _service(services, PgVectorIndex(), hybrid=False).search(employee, "annual leave days")
    assert any(h.keyword_rank for h in hybrid.hits)
    assert vector.hits and all(h.keyword_rank is None and h.vector_rank for h in vector.hits)


async def test_index_rows_hold_every_retrievable_chunk_with_its_vector(
    services: Services, world: World, corpus: dict[str, UUID]
) -> None:
    async with services.uow_factory(
        TenantContext(tenant_id=world.a.tenant.id, user_id="t", roles=("system",))
    ) as uow:
        rows = await uow.chunks.index_rows()
    assert rows and all(len(r.embedding) == 32 for r in rows)
    assert {r.document_id for r in rows} == {corpus["greek"], corpus["handbook"], corpus["restricted"]}
