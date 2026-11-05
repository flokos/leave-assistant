"""Seed data (FR-TEN-08, decided Q-07): two fictional companies. Every person, e-mail and fact is invented.

Aegean Logistics S.A. — Piraeus, Greek-first. Brightwater Software Ltd. — Dublin, English-first. Leave types,
policies, holidays and documents are added by later milestones; M1 seeds organisation and identities only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

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
class SeedCompany:
    slug: str
    name: str
    default_locale: Locale
    timezone: str
    departments: tuple[SeedDepartment, ...]
    employees: tuple[SeedEmployee, ...]
    settings: dict[str, object] = field(default_factory=dict)


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
)

COMPANIES: tuple[SeedCompany, ...] = (AEGEAN, BRIGHTWATER)
