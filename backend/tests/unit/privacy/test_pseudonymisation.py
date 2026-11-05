"""PseudonymisationService + vault: stable placeholders, known-entity variants, fail-closed keys, resolution."""

from __future__ import annotations

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import SecurityNotConfigured
from leave_assistant.application.ports import KnownEntity
from leave_assistant.application.privacy.service import PseudonymisationService
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.privacy.types import EntityType, LlmSafeText, find_placeholders
from leave_assistant.infrastructure.privacy.known_entities import known_entity_findings
from tests.conftest import principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.security import FakePiiEngine, fake_vault_cipher
from tests.fakes.system import FixedClock, SequentialIds
from tests.fakes.world import World

KNOWN = [
    KnownEntity(
        SequentialIds(500).new_id(), "Γιώργος", "Παπαδόπουλος", "g.papadopoulos@aegean.example", None
    ),
    KnownEntity(SequentialIds(501).new_id(), "Maria", "Oikonomou", "maria.oikonomou@aegean.example", None),
]


def test_known_entity_recogniser_matches_cases_initials_and_emails() -> None:
    text = (
        "Ο Γιώργος Παπαδόπουλος υπέβαλε αίτηση. Η αίτηση του Παπαδόπουλου εγκρίθηκε από τη Maria Oikonomou. "
        "Email: g.papadopoulos@aegean.example, Γ. Παπαδόπουλος υπογράφει. Άσχετη λέξη: Παπαδάκης."
    )
    findings = known_entity_findings(text, KNOWN)
    spans = [(f.entity_type, text[f.start : f.end], f.subject_employee_id) for f in findings]
    assert (EntityType.PERSON, "Γιώργος Παπαδόπουλος", KNOWN[0].employee_id) in spans
    assert (EntityType.PERSON, "Παπαδόπουλου", KNOWN[0].employee_id) in spans  # genitive, surname only
    assert (EntityType.PERSON, "Maria Oikonomou", KNOWN[1].employee_id) in spans
    assert (EntityType.EMAIL, "g.papadopoulos@aegean.example", KNOWN[0].employee_id) in spans
    assert (EntityType.PERSON, "Γ. Παπαδόπουλος", KNOWN[0].employee_id) in spans
    assert not any("Παπαδάκης" in s[1] for s in spans)


async def test_every_variant_of_a_known_employee_maps_to_one_stable_placeholder(
    services: Services, world: World, store: MemoryStore
) -> None:
    hr = principal_for(world.a.employees["hr_admin"])
    tenant_id = world.a.tenant.id
    privacy = services.privacy
    texts = [
        "Ο Γιώργος Παπαδόπουλος ζήτησε άδεια. Τηλ. 6971234567.",
        "Η αίτηση του Παπαδόπουλου εκκρεμεί· επικοινωνία στο g.papadopoulos@aegean.example.",
    ]
    results = []
    async with services.uow_factory(TenantContext.for_principal(hr)) as uow:
        for text in texts:
            findings = privacy.detect(text, language="el", known=KNOWN)
            results.append(
                await privacy.apply(
                    uow, tenant_id, text, findings, source_kind="document_version", source_id="v1"
                )
            )
        await uow.commit()
    first, second = results
    assert (
        isinstance(first.safe, LlmSafeText) and "Γιώργος" not in first.safe and "6971234567" not in first.safe
    )
    assert "PERSON_0001" in first.safe and "PHONE_0001" in first.safe
    assert (
        "PERSON_0001" in second.safe and "EMAIL_0001" in second.safe
    )  # the same person → the same placeholder
    assert first.counts == {"PERSON": 1, "PHONE": 1} and second.has_pii
    assert len(store.pii_vault) == 3 and all(e.tenant_id == tenant_id for e in store.pii_vault.values())
    person = next(e for e in store.pii_vault.values() if e.placeholder == "PERSON_0001")
    assert (
        person.subject_employee_id == KNOWN[0].employee_id
        and person.value_encrypted != "Γιώργος Παπαδόπουλος".encode()
    )
    assert person.source_kinds == ("document_version",)
    # resolution decrypts the original stored at first sight
    async with services.uow_factory(TenantContext.for_principal(hr)) as uow:
        resolved = await privacy.resolve(uow, tenant_id, "PERSON_0001")
        assert resolved is not None and resolved[0] == "Γιώργος Παπαδόπουλος"
        assert await privacy.resolve(uow, tenant_id, "PERSON_0099") is None
    assert [p.placeholder for p in find_placeholders(first.safe)] == ["PERSON_0001", "PHONE_0001"]


async def test_tenants_never_share_placeholders_or_vault_rows(
    services: Services, world: World, store: MemoryStore
) -> None:
    text = "Contact maria.oikonomou@aegean.example today."
    for tenant_world in (world.a, world.b):
        hr = principal_for(tenant_world.employees["hr_admin"])
        async with services.uow_factory(TenantContext.for_principal(hr)) as uow:
            result = await services.privacy.pseudonymise(
                uow, tenant_world.tenant.id, text, language="en", known=[], source_kind="user_message"
            )
            await uow.commit()
        assert result.safe == "Contact EMAIL_0001 today."  # each tenant starts its own sequence
    assert len(store.pii_vault) == 2 and len({e.tenant_id for e in store.pii_vault.values()}) == 2


async def test_vault_writes_fail_closed_without_keys(
    world: World, store: MemoryStore, clock: FixedClock, ids: SequentialIds
) -> None:
    from tests.fakes.memory import MemoryUnitOfWorkFactory

    service = PseudonymisationService(FakePiiEngine(), fake_vault_cipher(configured=False), ids, clock)
    assert not service.configured
    hr = principal_for(world.a.employees["hr_admin"])
    async with MemoryUnitOfWorkFactory(store)(TenantContext.for_principal(hr)) as uow:
        with pytest.raises(SecurityNotConfigured):
            await service.apply(
                uow, world.a.tenant.id, "x@y.example", [], source_kind="user_message", source_id=None
            )


def test_detection_threshold_and_overlap_merging() -> None:
    service = PseudonymisationService(
        FakePiiEngine(), fake_vault_cipher(), SequentialIds(), FixedClock(), threshold=0.75
    )
    findings = service.detect("AFM 123456789 and phone 6971234567 and 01018012345", language="en", known=[])
    # AFM scores 0.6 (< 0.75) and is dropped; AMKA 0.7 dropped; the phone (0.8) survives
    assert [f.entity_type for f in findings] == [EntityType.PHONE]
