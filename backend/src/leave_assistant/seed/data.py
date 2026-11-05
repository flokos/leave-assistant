"""Seed data (FR-TEN-08, decided Q-07): two fictional companies. Every person, e-mail and fact is invented.

Aegean Logistics S.A. — Piraeus, Greek-first. Brightwater Software Ltd. — Dublin, English-first. Leave types,
policies, holidays and documents are added by later milestones; M1 seeds organisation and identities only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from leave_assistant.domain.documents.types import Category
from leave_assistant.domain.leave.types import StepRole
from leave_assistant.domain.tenancy import Locale, Role


@dataclass(frozen=True, slots=True)
class SeedDepartment:
    code: str
    name: str
    manager_number: str | None = None


@dataclass(frozen=True, slots=True)
class SeedEmployee:
    number: str
    first_name: str
    last_name: str
    email: str
    department_code: str
    roles: frozenset[Role]
    hire_date: date
    locale: Locale | None = None  # defaults to the tenant locale


@dataclass(frozen=True, slots=True)
class SeedLeaveType:
    code: str
    name_el: str
    name_en: str
    balance_bearing: bool
    entitlement_days: Decimal | None  # policy for the seed year; None = no policy
    allow_retroactive_days: int = 0
    requires_document_above_days: int | None = None
    allow_negative: bool = False


@dataclass(frozen=True, slots=True)
class SeedHoliday:
    day: date
    name_el: str
    name_en: str
    recurring: bool


@dataclass(frozen=True, slots=True)
class SeedRule:
    steps: tuple[StepRole, ...]
    priority: int
    leave_type_code: str | None = None
    min_working_days: Decimal = Decimal("0")
    max_working_days: Decimal | None = None
    requires_document: bool = False
    auto_acknowledge: bool = False


@dataclass(frozen=True, slots=True)
class SeedRequest:
    """A sample request submitted `as_of` a given day; outcome drives approvals (`approved` = all steps, `rejected`, `cancelled`, `pending`)."""

    employee_number: str
    leave_type_code: str
    start: date
    end: date
    as_of: date
    outcome: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SeedDocument:
    """A corpus document (doc 05 §10): rendered from `seed/corpus/sources` by `scripts/build_corpus.py`."""

    slug: str  # source stem; the rendered file is `seed/corpus/files/<tenant>/<slug>.<format>`
    title: str
    category: Category
    format: str  # pdf | docx | md | txt | scan (image-only PDF → OCR)
    uploader_number: str
    department_code: str | None = None
    language_hint: str | None = None
    earlier_versions: tuple[str, ...] = ()  # source stems uploaded first, in order (supersede test)

    @property
    def extension(self) -> str:
        return "pdf" if self.format == "scan" else self.format


@dataclass(frozen=True, slots=True)
class SeedSupportingDocument:
    """A fictional supporting document attached to a seeded request (M5, doc 08 §9): `seed/corpus/supporting/<file>`."""

    employee_number: str
    request_start: date  # identifies the request together with the employee
    file: str


@dataclass(frozen=True, slots=True)
class SeedRetentionPolicy:
    """Placeholder retention periods (Q-08) — illustrative values, not legal advice."""

    document_class: str
    retention_days: int
    action: str = "delete_after_review"


@dataclass(frozen=True, slots=True)
class SeedCompany:
    slug: str
    name: str
    default_locale: Locale
    timezone: str
    departments: tuple[SeedDepartment, ...]
    employees: tuple[SeedEmployee, ...]
    settings: dict[str, object] = field(default_factory=dict)
    holidays: tuple[SeedHoliday, ...] = ()
    requests: tuple[SeedRequest, ...] = ()
    documents: tuple[SeedDocument, ...] = ()
    supporting_documents: tuple[SeedSupportingDocument, ...] = ()
    retention_policies: tuple[SeedRetentionPolicy, ...] = ()


# M5: every tenant starts with review-based placeholders (doc 07 §7, Q-08); company administrators adjust them
DEFAULT_RETENTION: tuple[SeedRetentionPolicy, ...] = (
    SeedRetentionPolicy("supporting_document", 365),
    SeedRetentionPolicy("company_document", 730),
)


SEED_YEAR = 2026
# Doc 01 §6.1 seed values; sick leave allows a negative balance so that cap-exceeding requests reach HR review (Q-02)
LEAVE_TYPES: tuple[SeedLeaveType, ...] = (
    SeedLeaveType("annual", "Κανονική άδεια", "Annual leave", True, Decimal("20")),
    SeedLeaveType(
        "sick",
        "Αναρρωτική άδεια",
        "Sick leave",
        True,
        Decimal("10"),
        allow_retroactive_days=30,
        requires_document_above_days=3,
        allow_negative=True,
    ),
    SeedLeaveType("unpaid", "Άδεια άνευ αποδοχών", "Unpaid leave", False, None),
)
# Doc 01 §6.4 routing table
APPROVAL_RULES: tuple[SeedRule, ...] = (
    SeedRule((), 10, "sick", max_working_days=Decimal("3"), auto_acknowledge=True),
    SeedRule((StepRole.HR,), 20, "sick", min_working_days=Decimal("3.01"), requires_document=True),
    SeedRule((StepRole.MANAGER,), 30, None, max_working_days=Decimal("5")),
    SeedRule((StepRole.MANAGER, StepRole.HR), 40, None, min_working_days=Decimal("5.01")),
)
CARRYOVER_MAX_DAYS = Decimal("5")


def _h(day: date, el: str, en: str, recurring: bool = False) -> SeedHoliday:
    return SeedHoliday(day, el, en, recurring)


GREEK_HOLIDAYS_2026 = (
    _h(date(2026, 1, 1), "Πρωτοχρονιά", "New Year's Day", True),
    _h(date(2026, 1, 6), "Θεοφάνεια", "Epiphany", True),
    _h(date(2026, 2, 23), "Καθαρά Δευτέρα", "Clean Monday"),
    _h(date(2026, 3, 25), "Εθνική εορτή 25ης Μαρτίου", "Independence Day", True),
    _h(date(2026, 4, 10), "Μεγάλη Παρασκευή", "Orthodox Good Friday"),
    _h(date(2026, 4, 13), "Δευτέρα του Πάσχα", "Orthodox Easter Monday"),
    _h(date(2026, 5, 1), "Πρωτομαγιά", "Labour Day", True),
    _h(date(2026, 6, 1), "Αγίου Πνεύματος", "Whit Monday"),
    _h(date(2026, 8, 15), "Κοίμηση της Θεοτόκου", "Assumption of Mary", True),
    _h(date(2026, 10, 28), "Εθνική εορτή 28ης Οκτωβρίου", "Ochi Day", True),
    _h(date(2026, 12, 25), "Χριστούγεννα", "Christmas Day", True),
    _h(date(2026, 12, 26), "Δεύτερη ημέρα Χριστουγέννων", "Second day of Christmas", True),
)
IRISH_HOLIDAYS_2026 = (
    _h(date(2026, 1, 1), "Πρωτοχρονιά", "New Year's Day", True),
    _h(date(2026, 2, 2), "Ημέρα Αγίας Βριγίτας", "St Brigid's Day"),
    _h(date(2026, 3, 17), "Ημέρα Αγίου Πατρικίου", "St Patrick's Day", True),
    _h(date(2026, 4, 6), "Δευτέρα του Πάσχα", "Easter Monday"),
    _h(date(2026, 5, 4), "Αργία Μαΐου", "May Bank Holiday"),
    _h(date(2026, 6, 1), "Αργία Ιουνίου", "June Bank Holiday"),
    _h(date(2026, 8, 3), "Αργία Αυγούστου", "August Bank Holiday"),
    _h(date(2026, 10, 26), "Αργία Οκτωβρίου", "October Bank Holiday"),
    _h(date(2026, 12, 25), "Χριστούγεννα", "Christmas Day", True),
    _h(date(2026, 12, 26), "Ημέρα Αγίου Στεφάνου", "St Stephen's Day", True),
)


E, M, H, C = Role.EMPLOYEE, Role.MANAGER, Role.HR_ADMIN, Role.COMPANY_ADMIN


def _emp(
    number: str,
    first: str,
    last: str,
    email: str,
    dept: str,
    roles: set[Role],
    hired: date,
    locale: Locale | None = None,
) -> SeedEmployee:
    return SeedEmployee(number, first, last, email, dept, frozenset(roles), hired, locale)


AEGEAN = SeedCompany(
    slug="aegean",
    name="Aegean Logistics S.A.",
    default_locale=Locale.EL,
    timezone="Europe/Athens",
    departments=(
        SeedDepartment("OPS", "Λειτουργίες", manager_number="AEG-003"),
        SeedDepartment("FIN", "Οικονομικές Υπηρεσίες", manager_number="AEG-006"),
        SeedDepartment("HR", "Ανθρώπινο Δυναμικό", manager_number="AEG-002"),
        SeedDepartment("WH", "Αποθήκη", manager_number="AEG-008"),
    ),
    employees=(
        _emp(
            "AEG-001",
            "Γιώργος",
            "Αλεξίου",
            "giorgos.alexiou@aegean-logistics.example",
            "HR",
            {C, E},
            date(2015, 3, 2),
        ),
        _emp(
            "AEG-002",
            "Μαρία",
            "Οικονόμου",
            "maria.oikonomou@aegean-logistics.example",
            "HR",
            {H, M, E},
            date(2017, 9, 11),
        ),
        _emp(
            "AEG-003",
            "Νίκος",
            "Παπαδάκης",
            "nikos.papadakis@aegean-logistics.example",
            "OPS",
            {M, E},
            date(2016, 1, 18),
        ),
        _emp(
            "AEG-004",
            "Ελένη",
            "Δημητρίου",
            "eleni.dimitriou@aegean-logistics.example",
            "OPS",
            {E},
            date(2021, 6, 1),
        ),
        _emp(
            "AEG-005",
            "Κώστας",
            "Μιχαηλίδης",
            "kostas.michailidis@aegean-logistics.example",
            "OPS",
            {E},
            date(2023, 2, 13),
        ),
        _emp(
            "AEG-006",
            "Σοφία",
            "Καραγιάννη",
            "sofia.karagianni@aegean-logistics.example",
            "FIN",
            {M, E},
            date(2018, 4, 9),
        ),
        _emp(
            "AEG-007",
            "Δημήτρης",
            "Αντωνίου",
            "dimitris.antoniou@aegean-logistics.example",
            "FIN",
            {E},
            date(2022, 10, 3),
        ),
        _emp(
            "AEG-008",
            "Αντώνης",
            "Λάμπρου",
            "antonis.lamprou@aegean-logistics.example",
            "WH",
            {M, E},
            date(2014, 11, 24),
        ),
        _emp(
            "AEG-009",
            "Χρήστος",
            "Βασιλείου",
            "christos.vasileiou@aegean-logistics.example",
            "WH",
            {E},
            date(2024, 5, 20),
        ),
        _emp(
            "AEG-010",
            "Anna",
            "Kowalski",
            "anna.kowalski@aegean-logistics.example",
            "OPS",
            {E},
            date(2025, 1, 7),
            Locale.EN,
        ),
    ),
    holidays=GREEK_HOLIDAYS_2026,
    requests=(
        SeedRequest(
            "AEG-004",
            "annual",
            date(2026, 9, 14),
            date(2026, 9, 18),
            date(2026, 9, 1),
            "approved",
            "Διακοπές",
        ),  # → COMPLETED by rollover
        SeedRequest(
            "AEG-005",
            "annual",
            date(2026, 12, 14),
            date(2026, 12, 18),
            date(2026, 11, 2),
            "approved",
            "Χριστούγεννα",
        ),
        SeedRequest(
            "AEG-007", "annual", date(2026, 12, 21), date(2026, 12, 23), date(2026, 11, 3), "pending"
        ),  # PENDING_MANAGER
        SeedRequest(
            "AEG-009",
            "annual",
            date(2026, 11, 23),
            date(2026, 12, 4),
            date(2026, 11, 2),
            "pending_hr",
            "Ταξίδι",
        ),  # > 5 days → HR pending
        SeedRequest(
            "AEG-010",
            "unpaid",
            date(2026, 12, 7),
            date(2026, 12, 8),
            date(2026, 11, 3),
            "rejected",
            "Personal",
        ),
        SeedRequest(
            "AEG-004", "sick", date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 3), "approved", "Ίωση"
        ),  # auto-acknowledged
        SeedRequest(
            "AEG-005",
            "annual",
            date(2026, 11, 9),
            date(2026, 11, 10),
            date(2026, 10, 20),
            "cancelled",
            "Αλλαγή σχεδίων",
        ),
    ),
    documents=(
        SeedDocument(
            "kanonismos-adeion",
            "Κανονισμός Αδειών",
            Category.POLICY_GENERAL,
            "pdf",
            "AEG-002",
            language_hint="el",
        ),
        SeedDocument(
            "leave-policy-en",
            "Leave Policy (English translation)",
            Category.POLICY_GENERAL,
            "docx",
            "AEG-002",
            language_hint="en",
        ),
        SeedDocument(
            "argies-2026", "Αργίες 2026", Category.POLICY_GENERAL, "md", "AEG-001", language_hint="el"
        ),
        SeedDocument(
            "diadikasia-anarrotikis",
            "Διαδικασία Αναρρωτικής Άδειας",
            Category.POLICY_GENERAL,
            "scan",
            "AEG-002",
            language_hint="el",
        ),
        SeedDocument(
            "odigos-exaireseon",
            "Οδηγός Εξαιρέσεων",
            Category.POLICY_HR_RESTRICTED,
            "docx",
            "AEG-002",
            language_hint="el",
        ),
        SeedDocument("faq", "Συχνές ερωτήσεις / FAQ", Category.POLICY_GENERAL, "md", "AEG-001"),
        # S07 distractor (doc 11 §6): "άδεια οδήγησης" (driving licence) is not leave
        SeedDocument(
            "odigoi-oximaton",
            "Οδηγοί εταιρικών οχημάτων: άδεια οδήγησης",
            Category.POLICY_GENERAL,
            "md",
            "AEG-001",
            language_hint="el",
        ),
        # M5 adversarial pair (doc 11 §5 S22/S23): the English override is quarantined, the Greek footnote goes to review
        SeedDocument(
            "adversarial-appendix-en",
            "Leave approvals appendix (untrusted upload)",
            Category.POLICY_GENERAL,
            "txt",
            "AEG-001",
            language_hint="en",
        ),
        SeedDocument(
            "adversarial-simeiosi-el",
            "Σημείωμα διαδικασίας (ύποπτο)",
            Category.POLICY_GENERAL,
            "md",
            "AEG-001",
            language_hint="el",
        ),
    ),
    supporting_documents=(SeedSupportingDocument("AEG-004", date(2026, 11, 2), "medical-note.png"),),
    retention_policies=DEFAULT_RETENTION,
)

BRIGHTWATER = SeedCompany(
    slug="brightwater",
    name="Brightwater Software Ltd.",
    default_locale=Locale.EN,
    timezone="Europe/Dublin",
    departments=(
        SeedDepartment("ENG", "Engineering", manager_number="BWS-003"),
        SeedDepartment("PROD", "Product", manager_number="BWS-006"),
        SeedDepartment("PPL", "People", manager_number="BWS-002"),
    ),
    employees=(
        _emp(
            "BWS-001", "Aoife", "Byrne", "aoife.byrne@brightwater.example", "PPL", {C, E}, date(2016, 8, 15)
        ),
        _emp(
            "BWS-002", "Ciara", "Walsh", "ciara.walsh@brightwater.example", "PPL", {H, M, E}, date(2018, 2, 5)
        ),
        _emp(
            "BWS-003", "Liam", "Murphy", "liam.murphy@brightwater.example", "ENG", {M, E}, date(2017, 5, 22)
        ),
        _emp("BWS-004", "Niamh", "Kelly", "niamh.kelly@brightwater.example", "ENG", {E}, date(2020, 9, 14)),
        _emp("BWS-005", "Sean", "O'Brien", "sean.obrien@brightwater.example", "ENG", {E}, date(2022, 3, 7)),
        _emp(
            "BWS-006", "Conor", "Doyle", "conor.doyle@brightwater.example", "PROD", {M, E}, date(2019, 1, 28)
        ),
        _emp("BWS-007", "Emma", "Ryan", "emma.ryan@brightwater.example", "PROD", {E}, date(2023, 7, 3)),
        _emp(
            "BWS-008",
            "Δήμητρα",
            "Νικολάου",
            "dimitra.nikolaou@brightwater.example",
            "ENG",
            {E},
            date(2024, 10, 1),
            Locale.EL,
        ),
    ),
    holidays=IRISH_HOLIDAYS_2026,
    requests=(
        SeedRequest(
            "BWS-004", "annual", date(2026, 8, 10), date(2026, 8, 14), date(2026, 7, 20), "approved", "Summer"
        ),  # → COMPLETED
        SeedRequest(
            "BWS-005",
            "annual",
            date(2026, 12, 21),
            date(2026, 12, 24),
            date(2026, 11, 2),
            "approved",
            "Christmas",
        ),
        SeedRequest(
            "BWS-007", "annual", date(2026, 12, 14), date(2026, 12, 15), date(2026, 11, 3), "pending"
        ),
        SeedRequest(
            "BWS-008",
            "annual",
            date(2026, 11, 30),
            date(2026, 12, 11),
            date(2026, 11, 2),
            "pending_hr",
            "Επίσκεψη στην Ελλάδα",
        ),
        SeedRequest(
            "BWS-004", "sick", date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 4), "approved"
        ),  # auto-acknowledged
        SeedRequest(
            "BWS-005",
            "unpaid",
            date(2026, 11, 16),
            date(2026, 11, 17),
            date(2026, 11, 2),
            "rejected",
            "Personal matters",
        ),
    ),
    documents=(
        SeedDocument(
            "employee-leave-handbook",
            "Employee Leave Handbook",
            Category.POLICY_GENERAL,
            "pdf",
            "BWS-002",
            language_hint="en",
            earlier_versions=("employee-leave-handbook-v1",),
        ),
        SeedDocument(
            "public-holidays-2026",
            "Public Holidays 2026",
            Category.POLICY_GENERAL,
            "md",
            "BWS-001",
            language_hint="en",
        ),
        SeedDocument(
            "sick-leave-procedure",
            "Sick Leave Procedure",
            Category.POLICY_GENERAL,
            "docx",
            "BWS-002",
            language_hint="en",
        ),
        SeedDocument(
            "exceptional-leave-guidelines",
            "Exceptional Leave Guidelines",
            Category.POLICY_HR_RESTRICTED,
            "pdf",
            "BWS-002",
            language_hint="en",
        ),
        SeedDocument(
            "release-freeze-dates",
            "Engineering release freeze dates 2026",
            Category.DEPARTMENT_NOTICE,
            "txt",
            "BWS-003",
            department_code="ENG",
            language_hint="en",
        ),
        SeedDocument(
            "adversarial-vendor-memo-en",
            "Vendor memo (untrusted upload)",
            Category.POLICY_GENERAL,
            "txt",
            "BWS-001",
            language_hint="en",
        ),
        SeedDocument(
            "adversarial-simeiosi-el",
            "Σημείωμα για το ελληνικό γραφείο (ύποπτο)",
            Category.POLICY_GENERAL,
            "md",
            "BWS-001",
            language_hint="el",
        ),
    ),
    supporting_documents=(SeedSupportingDocument("BWS-004", date(2026, 11, 3), "medical-note.png"),),
    retention_policies=DEFAULT_RETENTION,
)

COMPANIES: tuple[SeedCompany, ...] = (AEGEAN, BRIGHTWATER)
