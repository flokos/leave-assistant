"""Shared helpers for document tests: uploads from bytes, Greek/English policy fixtures."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.documents.commands import CreateDocument, Upload
from leave_assistant.application.documents.views import DocumentSummary
from leave_assistant.domain.documents.types import Category
from leave_assistant.domain.tenancy import Principal

GREEK_POLICY_MD = """# Κανονισμός Αδειών

Ο παρών κανονισμός ορίζει τους τύπους άδειας και τη διαδικασία αίτησης για όλους τους εργαζομένους.

## 3. Κανονική άδεια

Κάθε εργαζόμενος δικαιούται είκοσι εργάσιμες ημέρες κανονικής άδειας ανά ημερολογιακό έτος.
Οι εργαζόμενοι υποβάλλουν αίτηση τουλάχιστον τρεις εργάσιμες ημέρες πριν την έναρξη της άδειας.

### 3.2 Μεταφορά υπολοίπου

Έως πέντε ημέρες κανονικής άδειας μεταφέρονται στο επόμενο έτος και λήγουν στις 31 Μαρτίου.

## 4. Αναρρωτική άδεια

Η αναρρωτική άδεια έως τρεις ημέρες δηλώνεται χωρίς δικαιολογητικό. Πάνω από τρεις ημέρες απαιτείται ιατρική βεβαίωση.

| Ημερομηνία | Αργία |
| --- | --- |
| 1 Ιανουαρίου | Πρωτοχρονιά |
| 25 Μαρτίου | Εθνική εορτή |
"""

ENGLISH_HANDBOOK_MD = """# Employee Leave Handbook

## 2. Annual leave

Every employee is entitled to twenty working days of annual leave per calendar year, pro-rated in the hire year.
Requests must be submitted at least three working days before the leave starts.

### 2.1 Carry-over

Up to five unused days carry over into the next year and expire on 31 March.

## 5. Sick leave

Sick leave of up to three days is self-declared; a medical certificate is required beyond three days.
"""

HR_GUIDE_MD = """# Exceptional Leave Guidelines

## 1. Compassionate leave

HR may grant up to five days of paid compassionate leave after reviewing the circumstances with the manager.
"""


def make_upload(tmp_path: Path, name: str, content: bytes, *, declared: str | None = None) -> Upload:
    path = tmp_path / name
    path.write_bytes(content)
    return Upload(path=path, filename=name, declared_mime=declared, size_bytes=len(content))


async def create_markdown(
    services: Services,
    principal: Principal,
    tmp_path: Path,
    *,
    title: str,
    content: str,
    category: Category = Category.POLICY_GENERAL,
    department_id: UUID | None = None,
    name: str = "doc.md",
) -> DocumentSummary:
    upload = make_upload(tmp_path, name, content.encode(), declared="text/markdown")
    return await services.documents.create(
        principal, CreateDocument(title=title, category=category, upload=upload, department_id=department_id)
    )


@pytest.fixture
def uploads(tmp_path: Path) -> Path:
    return tmp_path
