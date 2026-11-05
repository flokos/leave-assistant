"""NFR-PERF-02 measurement: ingest a 30-page text PDF and a 10-page scanned PDF through the running stack.

Usage: `uv run --group corpus python scripts/perf_ingest.py` with the stack up (`make up-staged`, `make bootstrap`,
`make models`). Uploads as the Aegean HR administrator, waits for ACTIVE and prints wall-clock times; the documents
are deleted afterwards so the seed stays reusable.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_corpus import Element, render_pdf, render_scan  # noqa: E402

API = os.environ.get("E2E_API_URL", f"http://localhost:{os.environ.get('FRONTEND_PORT', '8080')}/api/v1")
KEYCLOAK = os.environ.get("KC_PUBLIC_URL", f"http://localhost:{os.environ.get('KEYCLOAK_PORT', '8081')}")
PASSWORD = os.environ.get("SEED_USER_PASSWORD", "leave-dev")
USER = "maria.oikonomou@aegean-logistics.example"

PARAGRAPH = (
    "Κάθε εργαζόμενος δικαιούται είκοσι εργάσιμες ημέρες κανονικής άδειας ανά ημερολογιακό έτος. Η αίτηση "
    "υποβάλλεται τουλάχιστον τρεις εργάσιμες ημέρες πριν από την έναρξη της άδειας και εγκρίνεται από τον "
    "προϊστάμενο του τμήματος. Every employee is entitled to twenty working days of annual leave per calendar "
    "year; requests are submitted at least three working days in advance and approved by the department manager."
)


def synthetic(pages: int, paragraphs_per_page: int) -> list[Element]:
    elements: list[Element] = [Element("heading", "Συνθετικός κανονισμός για μέτρηση απόδοσης", 1)]
    for page in range(1, pages + 1):
        elements.append(Element("heading", f"{page}. Ενότητα {page} / Section {page}", 2))
        for index in range(paragraphs_per_page):
            elements.append(Element("paragraph", f"{page}.{index + 1} {PARAGRAPH}"))
        if page % 5 == 0:
            elements.append(
                Element(
                    "table",
                    rows=[
                        ["Ημερομηνία", "Αργία"],
                        ["1 Ιανουαρίου", "Πρωτοχρονιά"],
                        ["25 Μαρτίου", "Εθνική εορτή"],
                    ],
                )
            )
    return elements


def token(http: httpx.Client) -> str:
    response = http.post(
        f"{KEYCLOAK}/realms/leave-assistant/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "frontend-spa", "username": USER, "password": PASSWORD},
    )
    response.raise_for_status()
    return str(response.json()["access_token"])


def measure(http: httpx.Client, headers: dict[str, str], path: Path, title: str) -> dict[str, object]:
    started = time.monotonic()
    with path.open("rb") as handle:
        created = http.post(
            f"{API}/documents",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            data={"title": title, "category": "policy_general"},
            files={"file": (path.name, handle, "application/pdf")},
        )
    created.raise_for_status()
    document_id = created.json()["id"]
    uploaded = time.monotonic()
    while True:
        detail = http.get(f"{API}/documents/{document_id}", headers=headers)
        detail.raise_for_status()
        version = detail.json()["versions"][-1]
        if version["status"] in {"ACTIVE", "FAILED"}:
            break
        time.sleep(2)
    finished = time.monotonic()
    chunks = http.get(f"{API}/documents/{document_id}/versions/1", headers=headers).json()["chunk_count"]
    http.delete(
        f"{API}/documents/{document_id}",
        params={"version": detail.json()["version"]},
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    return {
        "title": title,
        "size_kb": round(path.stat().st_size / 1024),
        "status": version["status"],
        "pages": version["pages"],
        "chunks": chunks,
        "upload_s": round(uploaded - started, 1),
        "total_s": round(finished - started, 1),
        "error": version["processing_error"],
    }


def main(only: str | None) -> int:
    with tempfile.TemporaryDirectory() as directory, httpx.Client(timeout=120.0) as http:
        headers = {"Authorization": f"Bearer {token(http)}"}
        cases: list[tuple[Path, str]] = []
        if only in (None, "text"):
            text_pdf = Path(directory) / "perf-text-30p.pdf"
            render_pdf(synthetic(30, 9), text_pdf)
            cases.append((text_pdf, "Perf: 30-page text PDF"))
        if only in (None, "scan"):
            scan_pdf = Path(directory) / "perf-scan-10p.pdf"
            render_scan(synthetic(10, 5), scan_pdf)
            cases.append((scan_pdf, "Perf: 10-page scanned PDF"))
        for path, title in cases:
            result = measure(http, headers, path, title)
            sys.stdout.write(f"{result}\n")
            headers = {"Authorization": f"Bearer {token(http)}"}
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
