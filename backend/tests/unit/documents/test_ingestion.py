"""IngestionPipeline with fakes: activation + supersede, failures (no text, parser error, storage), reindex."""

from __future__ import annotations

from pathlib import Path

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.documents.ingestion import IngestionError
from leave_assistant.domain.documents.types import ChunkType, VersionStatus
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.world import World
from tests.unit.documents.conftest import ENGLISH_HANDBOOK_MD, GREEK_POLICY_MD, create_markdown, make_upload


async def test_process_activates_version_with_chunks_vectors_language_and_bookkeeping(
    services: Services, world: World, store: MemoryStore, uploads: Path
) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    created = await create_markdown(services, hr, uploads, title="Κανονισμός Αδειών", content=GREEK_POLICY_MD)
    document, version = created.document, created.latest_version
    assert version is not None
    outcome = await services.ingestion.process(document.tenant_id, version.id)
    assert outcome.status is VersionStatus.ACTIVE and outcome.chunks >= 3 and outcome.language == "el"
    stored = store.document_versions[version.id]
    assert stored.status is VersionStatus.ACTIVE and stored.retrievable and stored.processed_at is not None
    assert stored.index_generation == "fake-e5@test|v1" and stored.extracted_object_key is not None
    assert stored.attempts == 1 and stored.pages == 1 and stored.risk_tier.value == "low"
    assert store.documents[document.id].active_version_id == version.id
    chunks = sorted((c for c in store.chunks.values() if c.version_id == version.id), key=lambda c: c.ordinal)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(c.embedding is not None and len(c.embedding) == 32 and c.retrievable for c in chunks)
    assert all(c.embedding_model_id == "fake-e5@test" and c.language == "el" for c in chunks)
    assert all(c.content_llm_safe == c.content and not c.has_pii for c in chunks)
    assert any(c.chunk_type is ChunkType.TABLE and "Πρωτοχρονιά" in c.content for c in chunks)
    assert any("3.2 Μεταφορά υπολοίπου" in c.heading_path for c in chunks)
    assert (world.a.tenant.id, "fake-e5@test|v1") in store.index_generations
    actions = [a.action for a in store.audit]
    assert actions[-2:] == ["document.version.activated", "document.processed"]
    assert [n.type for n in store.notifications.values()] == ["document.processed"]
    assert [e.type for e in store.integration_events] == [
        "document.uploaded",
        "document.processed",
        "notification.created",
    ]
    extracted = services.adapters.storage.objects[stored.extracted_object_key][0]  # type: ignore[attr-defined]
    assert "Κανονισμός".encode() in extracted
    # idempotent: processing an active version again is a no-op
    again = await services.ingestion.process(document.tenant_id, version.id)
    assert again.skipped and again.status is VersionStatus.ACTIVE


async def test_new_active_version_supersedes_the_previous_one(
    services: Services, world: World, store: MemoryStore, uploads: Path
) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    created = await create_markdown(
        services, hr, uploads, title="Handbook", content=ENGLISH_HANDBOOK_MD.replace("twenty", "twenty-five")
    )
    document, v1 = created.document, created.latest_version
    assert v1 is not None
    await services.ingestion.process(document.tenant_id, v1.id)
    v2 = await services.documents.add_version(
        hr, document.id, make_upload(uploads, "v2.md", ENGLISH_HANDBOOK_MD.encode())
    )
    await services.ingestion.process(document.tenant_id, v2.id)
    assert (
        store.document_versions[v1.id].status is VersionStatus.SUPERSEDED
        and not store.document_versions[v1.id].retrievable
    )
    assert (
        store.document_versions[v2.id].status is VersionStatus.ACTIVE
        and store.documents[document.id].active_version_id == v2.id
    )
    assert all(not c.retrievable for c in store.chunks.values() if c.version_id == v1.id)
    assert all(c.retrievable for c in store.chunks.values() if c.version_id == v2.id)
    employee = principal_for(world.a.employees["employee"])
    bundle = await services.retrieval.search(employee, "annual leave twenty days")
    assert bundle.hits and {h.version.id for h in bundle.hits} == {v2.id}


async def test_failures_are_recorded_and_the_final_one_fails_the_version(
    services: Services, world: World, store: MemoryStore, uploads: Path
) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    created = await services.documents.create(
        hr,
        __import__(
            "leave_assistant.application.documents.commands", fromlist=["CreateDocument"]
        ).CreateDocument(
            "Scan",
            __import__(
                "leave_assistant.domain.documents.types", fromlist=["Category"]
            ).Category.POLICY_GENERAL,
            make_upload(uploads, "scan.pdf", b"%PDF-1.4\n%binary without text"),
        ),
    )
    document, version = created.document, created.latest_version
    assert version is not None
    with pytest.raises(IngestionError) as info:
        await services.ingestion.process(document.tenant_id, version.id)
    assert info.value.reason == "no_text"
    assert store.document_versions[version.id].status is VersionStatus.PROCESSING
    await services.ingestion.record_failure(document.tenant_id, version.id, "no_text", final=False)
    assert store.document_versions[version.id].status is VersionStatus.PROCESSING
    assert store.document_versions[version.id].processing_error == "no_text"
    assert not [n for n in store.notifications.values() if n.type == "document.failed"]
    await services.ingestion.record_failure(document.tenant_id, version.id, "no_text", final=True)
    failed = store.document_versions[version.id]
    assert failed.status is VersionStatus.FAILED and not failed.retrievable
    assert [n.type for n in store.notifications.values()] == ["document.failed"]
    assert "document.failed" in [a.action for a in store.audit]
    assert [e.type for e in store.integration_events][-2:] == ["document.failed", "notification.created"]
    # parser exceptions become stable reasons
    services.adapters.parser.error = RuntimeError("docling exploded")  # type: ignore[attr-defined]
    store.document_versions[version.id].status = VersionStatus.UPLOADED
    with pytest.raises(IngestionError) as info2:
        await services.ingestion.process(document.tenant_id, version.id)
    assert info2.value.reason == "parse_error" and "docling exploded" in info2.value.detail


async def test_storage_failure_leaves_no_partial_index(
    services: Services, world: World, store: MemoryStore, uploads: Path
) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    created = await create_markdown(services, hr, uploads, title="Handbook", content=ENGLISH_HANDBOOK_MD)
    document, version = created.document, created.latest_version
    assert version is not None
    services.adapters.storage.fail_put = True  # type: ignore[attr-defined]
    with pytest.raises(OSError):
        await services.ingestion.process(document.tenant_id, version.id)
    assert not [c for c in store.chunks.values() if c.version_id == version.id]
    assert store.document_versions[version.id].status is VersionStatus.PROCESSING
    services.adapters.storage.fail_put = False  # type: ignore[attr-defined]
    outcome = await services.ingestion.process(document.tenant_id, version.id)
    assert outcome.status is VersionStatus.ACTIVE and store.document_versions[version.id].attempts == 2


async def test_reindex_rebuilds_chunks_from_the_stored_structure(
    services: Services, world: World, store: MemoryStore, uploads: Path
) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    created = await create_markdown(services, hr, uploads, title="Handbook", content=ENGLISH_HANDBOOK_MD)
    document, version = created.document, created.latest_version
    assert version is not None
    first = await services.ingestion.process(document.tenant_id, version.id)
    before = {c.id: c.content for c in store.chunks.values() if c.version_id == version.id}
    services.adapters.parser.calls.clear()  # type: ignore[attr-defined]
    outcome = await services.ingestion.reindex(document.tenant_id, version.id)
    assert outcome.chunks == first.chunks and not services.adapters.parser.calls  # type: ignore[attr-defined]
    after = {c.id: c.content for c in store.chunks.values() if c.version_id == version.id}
    assert after == before and all(c.retrievable for c in store.chunks.values() if c.version_id == version.id)
    assert [a.action for a in store.audit][-1] == "document.reindexed"
