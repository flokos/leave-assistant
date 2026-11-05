"""RetrievalService: hybrid hits under visibility, citations, per-document cap, validation."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import AuthorizationError, InvalidInputError, NotFoundError
from leave_assistant.application.retrieval.service import NotRetrievable
from leave_assistant.domain.documents.types import Category
from leave_assistant.domain.tenancy import Principal, Role
from tests.conftest import principal_for
from tests.fakes.world import World
from tests.unit.documents.conftest import ENGLISH_HANDBOOK_MD, GREEK_POLICY_MD, HR_GUIDE_MD, create_markdown


async def ingest(
    services: Services,
    principal: Principal,
    uploads: Path,
    title: str,
    content: str,
    category: Category = Category.POLICY_GENERAL,
) -> UUID:
    created = await create_markdown(
        services, principal, uploads, title=title, content=content, category=category, name=f"{title}.md"
    )
    assert created.latest_version is not None
    await services.ingestion.process(created.document.tenant_id, created.latest_version.id)
    return created.document.id


@pytest.fixture
async def corpus(services: Services, world: World, uploads: Path) -> dict[str, UUID]:
    hr = principal_for(world.a.employees["hr_admin"])
    return {
        "greek": await ingest(services, hr, uploads, "Κανονισμός Αδειών", GREEK_POLICY_MD),
        "handbook": await ingest(services, hr, uploads, "Employee Leave Handbook", ENGLISH_HANDBOOK_MD),
        "restricted": await ingest(
            services, hr, uploads, "Exceptional Leave Guidelines", HR_GUIDE_MD, Category.POLICY_HR_RESTRICTED
        ),
    }


async def test_search_respects_visibility_and_returns_citations(
    services: Services, world: World, corpus: dict[str, UUID]
) -> None:
    employee = principal_for(world.a.employees["employee"])
    hr = principal_for(world.a.employees["hr_admin"])
    bundle = await services.retrieval.search(employee, "μεταφορά υπολοίπου κανονικής άδειας")
    assert bundle.language == "el" and bundle.hits
    top = bundle.hits[0]
    assert top.document.id == corpus["greek"] and top.score > 0
    # the carry-over clause is among the leading hits (exact ranking is the real model's business, M9)
    clause = next(h for h in bundle.hits[:3] if "3.2 Μεταφορά υπολοίπου" in h.citation.heading_path)
    assert clause.citation.title == "Κανονισμός Αδειών" and clause.citation.version_no == 1
    assert clause.citation.page_start == 1 and (clause.keyword_rank or clause.vector_rank)
    assert corpus["restricted"] not in {h.document.id for h in bundle.hits}
    employee_hits = await services.retrieval.search(employee, "compassionate leave")
    assert corpus["restricted"] not in {h.document.id for h in employee_hits.hits}
    hr_hits = await services.retrieval.search(hr, "compassionate leave")
    assert hr_hits.hits and hr_hits.hits[0].document.id == corpus["restricted"] and hr_hits.language == "en"
    assert len((await services.retrieval.search(employee, "leave", k=2)).hits) <= 2
    # other tenant: no hits at all
    other = principal_for(world.b.employees["employee"])
    assert not (await services.retrieval.search(other, "annual leave twenty days")).hits


async def test_per_document_cap(services: Services, world: World, uploads: Path) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    sections = "\n\n".join(
        f"## Section {n}\n\nAnnual leave clause number {n} about annual leave days." for n in range(1, 9)
    )
    doc = await ingest(services, hr, uploads, "Big policy", "# Big policy\n\n" + sections)
    bundle = await services.retrieval.search(hr, "annual leave days", k=10)
    assert sum(1 for h in bundle.hits if h.document.id == doc) <= 3


async def test_citation_rules(services: Services, world: World, corpus: dict[str, UUID]) -> None:
    employee = principal_for(world.a.employees["employee"])
    hr = principal_for(world.a.employees["hr_admin"])
    hit = (await services.retrieval.search(hr, "compassionate leave")).hits[0]
    cited = await services.retrieval.citation(hr, hit.chunk.id)
    assert cited.chunk.id == hit.chunk.id and cited.citation.title == "Exceptional Leave Guidelines"
    with pytest.raises(NotFoundError):
        await services.retrieval.citation(employee, hit.chunk.id)
    with pytest.raises(NotFoundError):
        await services.retrieval.citation(hr, UUID(int=0))
    await services.documents.retire(hr, corpus["restricted"], 1)
    with pytest.raises(NotRetrievable) as info:
        await services.retrieval.citation(hr, hit.chunk.id)
    assert info.value.problem_type == "document/not-retrievable"
    assert not [
        h
        for h in (await services.retrieval.search(hr, "compassionate leave")).hits
        if h.document.id == corpus["restricted"]
    ]


async def test_query_validation_and_platform_admin(services: Services, world: World) -> None:
    employee = principal_for(world.a.employees["employee"])
    with pytest.raises(InvalidInputError):
        await services.retrieval.search(employee, "   ")
    with pytest.raises(InvalidInputError):
        await services.retrieval.search(employee, "x" * 501)
    platform = Principal(user_id="p", username="p", roles=frozenset({Role.PLATFORM_ADMIN}))
    with pytest.raises(AuthorizationError):
        await services.retrieval.search(platform, "leave")
