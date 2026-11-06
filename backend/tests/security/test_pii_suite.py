"""PII suite with the real Presidio engine (doc 12 M5 exit criteria; FR-PII-01..05): Greek and English names,
AMKA/AFM with checksums, phones, e-mails, IBANs, known employees in every grammatical case, LLM-safe output.

Runs on every commit (spaCy models are venv packages); one session-scoped engine (≈ 20 s to load)."""

from __future__ import annotations

from uuid import UUID

import pytest

from leave_assistant.application.ports import KnownEntity
from leave_assistant.application.privacy.service import PseudonymisationService
from leave_assistant.domain.privacy.types import EntityType, LlmSafeText, find_placeholders
from leave_assistant.infrastructure.privacy.presidio_engine import PresidioEngine
from tests.fakes.security import fake_vault_cipher
from tests.fakes.system import FixedClock, SequentialIds

pytestmark = pytest.mark.security

KNOWN = [
    KnownEntity(UUID(int=1), "Γιώργος", "Αλεξίου", "giorgos.alexiou@aegean-logistics.example", None),
    KnownEntity(UUID(int=2), "Ελένη", "Δημητρίου", "eleni.dimitriou@aegean-logistics.example", None),
    KnownEntity(UUID(int=3), "Aoife", "Byrne", "aoife.byrne@brightwater.example", None),
]
AMKA = "01018012342"  # valid checksum (fictional)
AFM = "123456783"  # valid mod-11 check digit (fictional)


@pytest.fixture(scope="session")
def engine() -> PresidioEngine:
    engine = PresidioEngine(threshold=0.4)
    engine.load()
    return engine


@pytest.fixture
def service(engine: PresidioEngine) -> PseudonymisationService:
    return PseudonymisationService(engine, fake_vault_cipher(), SequentialIds(), FixedClock())


def types_of(findings) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
    out: dict[str, list[str]] = {}
    for f in findings:
        out.setdefault(f.entity_type.value, []).append(f.text)
    return out


def test_greek_identifiers_with_checksums_and_context(service: PseudonymisationService) -> None:
    text = (
        f"Ο εργαζόμενος με ΑΜΚΑ {AMKA} και ΑΦΜ {AFM} δήλωσε ασθένεια. Τηλέφωνο επικοινωνίας 6971234567, "
        "σταθερό 2101234567. IBAN GR1601101250000000012300695 για την αποζημίωση."
    )
    found = types_of(service.detect(text, language="el", known=[]))
    assert found["AMKA"] == [AMKA] and found["AFM"] == [AFM]
    assert set(found["PHONE"]) >= {"6971234567"}
    assert any(v.replace(" ", "").startswith("GR16") for v in found["IBAN"])
    # invalid checksums are ignored even with context (false-positive guard)
    nothing = types_of(service.detect("ΑΜΚΑ 01018012345 και ΑΦΜ 123456789", language="el", known=[]))
    assert "AMKA" not in nothing and "AFM" not in nothing


def test_names_and_emails_in_both_languages(service: PseudonymisationService) -> None:
    greek = service.detect(
        "Η κυρία Μαρία Οικονόμου ενημέρωσε τον Νίκο Παπαδάκη στο nikos.papadakis@aegean-logistics.example.",
        language="el",
        known=[],
    )
    found = types_of(greek)
    assert any("Οικονόμου" in v for v in found.get("PERSON", [])), found
    assert found["EMAIL"] == ["nikos.papadakis@aegean-logistics.example"]
    english = types_of(
        service.detect(
            "Ciara Walsh approved the request submitted by Liam O'Connor last Monday.",
            language="en",
            known=[],
        )
    )
    assert any("Ciara Walsh" in v for v in english.get("PERSON", [])), english


def test_known_employees_are_caught_in_every_case_and_map_to_one_placeholder(
    service: PseudonymisationService,
) -> None:
    text = (
        "Ο Γιώργος Αλεξίου ενέκρινε την αίτηση της Ελένης Δημητρίου. Η αίτηση του Αλεξίου καταγράφηκε· "
        "η Δημητρίου ενημερώθηκε στο eleni.dimitriou@aegean-logistics.example. Aoife Byrne was copied; "
        "Byrne's team lead was not."
    )
    findings = service.detect(text, language="el", known=KNOWN)
    known_people = {
        (f.text, f.subject_employee_id)
        for f in findings
        if f.source == "known_entity" and f.entity_type is EntityType.PERSON
    }
    assert {
        ("Γιώργος Αλεξίου", UUID(int=1)),
        ("Αλεξίου", UUID(int=1)),
        ("Δημητρίου", UUID(int=2)),
    } <= known_people
    assert ("Aoife Byrne", UUID(int=3)) in known_people and any(
        t.startswith("Byrne") for t, _ in known_people
    )
    assert any(f.entity_type is EntityType.EMAIL and f.subject_employee_id == UUID(int=2) for f in findings)
    assert all(
        f.subject_employee_id is not None
        for f in findings
        if f.entity_type is EntityType.PERSON and f.source == "known_entity"
    )


async def test_llm_safe_text_has_no_identifiers_and_placeholders_are_stable(
    service: PseudonymisationService,
) -> None:
    from leave_assistant.application.tenancy.context import TenantContext
    from tests.fakes.memory import MemoryStore, MemoryUnitOfWorkFactory

    tenant_id = UUID(int=42)
    store = MemoryStore()
    text1 = f"Ο Γιώργος Αλεξίου (ΑΜΚΑ {AMKA}, τηλ. 6971234567) ζήτησε άδεια."
    text2 = "Η αίτηση του Αλεξίου εγκρίθηκε· τηλέφωνο 6971234567."
    results = []
    async with MemoryUnitOfWorkFactory(store)(TenantContext(tenant_id=tenant_id, user_id="t")) as uow:
        for text in (text1, text2):
            results.append(
                await service.pseudonymise(
                    uow, tenant_id, text, language="el", known=KNOWN, source_kind="document_version"
                )
            )
        await uow.commit()
    first, second = results
    assert isinstance(first.safe, LlmSafeText)
    for raw in ("Γιώργος", "Αλεξίου", AMKA, "6971234567"):
        assert raw not in first.safe and raw not in second.safe
    placeholders1 = {p.placeholder for p in find_placeholders(first.safe)}
    placeholders2 = {p.placeholder for p in find_placeholders(second.safe)}
    assert (
        "PERSON_0001" in placeholders1 and "PERSON_0001" in placeholders2
    )  # nominative and genitive → one placeholder
    assert "PHONE_0001" in placeholders1 and "PHONE_0001" in placeholders2
    assert len(store.pii_vault) == 3


def test_benign_policy_text_yields_few_or_no_findings(service: PseudonymisationService) -> None:
    text = (
        "Κάθε εργαζόμενος δικαιούται είκοσι εργάσιμες ημέρες κανονικής άδειας ανά ημερολογιακό έτος. "
        "Οι αιτήσεις υποβάλλονται τουλάχιστον τρεις εργάσιμες ημέρες πριν την έναρξη. "
        "Requests of up to five working days are approved by the department manager."
    )
    findings = service.detect(text, language="el", known=KNOWN)
    assert not [
        f
        for f in findings
        if f.entity_type in (EntityType.AMKA, EntityType.AFM, EntityType.PHONE, EntityType.EMAIL)
    ]


def test_request_numbers_are_never_personal_data(service: PseudonymisationService) -> None:
    # Presidio's phone pattern took the digits of LR-2026-000007 (M7): request numbers stay readable for the tools
    for text in (
        "Approve request LR-2026-000007 please",
        "Ακύρωσε την αίτηση LR-2026-000123 του PERSON_0001",
    ):
        findings = service.detect(text, language="en", known=[])
        assert not any("2026-000" in f.text for f in findings), findings
    assert service.detect("Call me on 210 123 4567", language="en", known=[])  # phones are still found
