"""PII precision on the seed corpus (M10, R-30; doc 13 R-30, rag-quality.md §3.2).

    cd backend && uv run --no-sync python -m leave_assistant.evaluation.pii_precision

Runs the real detection (Presidio + spaCy + the known-entity layer) on every retrievable chunk of both seed tenants,
twice: with the conversation profile (no document filter: what M5–M9 stored in `content_llm_safe`) and with the
document profile (`application/privacy/filters.py`). No vault writes, read-only. Every PERSON/ADDRESS finding is
listed with its verdict under each profile, so precision can be labelled by hand; the report counts the findings
per type and source and records which spans the document profile drops.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from typing import Any

from leave_assistant.application.ports import KnownEntity
from leave_assistant.application.privacy.filters import organisational_terms
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.evaluation.harness import RESULTS, Principals, git_commit, host_settings, snapshot, stamp
from leave_assistant.infrastructure.runtime import build_runtime

WATCHED = ("PERSON", "ADDRESS")


async def _tenant_inputs(services: Any, tenant_id: Any) -> tuple[list[KnownEntity], tuple[str, ...]]:
    async with services.uow_factory(
        TenantContext(tenant_id=tenant_id, user_id="eval", roles=("system",))
    ) as uow:
        known = [
            KnownEntity(e.id, e.first_name, e.last_name, e.email, e.department_id)
            for e in await uow.employees.list_active()
        ]
        terms = organisational_terms(d.name for d in await uow.departments.list())
    return known, terms


async def run() -> dict[str, Any]:
    runtime = build_runtime(host_settings(), role="api")
    try:
        services = runtime.services
        principals = Principals(services)
        corpus = await snapshot(services, principals)
        privacy = services.privacy
        inputs = {
            slug: await _tenant_inputs(services, await principals.tenant_id(slug))
            for slug in corpus.documents
        }
        findings: list[dict[str, Any]] = []
        counts: dict[str, Counter[str]] = {"conversation": Counter(), "document": Counter()}
        by_document: dict[tuple[str, str], list[str]] = {}
        for chunk in corpus.chunks:
            by_document.setdefault((chunk.tenant, chunk.title), []).append(chunk.content)
        # ingestion detects with the document's language (detected on its first 20,000 characters), not the chunk's
        languages = {
            key: services.adapters.language.detect("\n".join(texts)[:20000])
            for key, texts in by_document.items()
        }
        for chunk in corpus.chunks:
            known, terms = inputs[chunk.tenant]
            language = languages[(chunk.tenant, chunk.title)]
            plain = privacy.detect(chunk.content, language=language, known=known)
            kept = privacy.detect(chunk.content, language=language, known=known, document_terms=terms)
            kept_spans = {(f.start, f.end, f.entity_type.value) for f in kept}
            for profile, rows in (("conversation", plain), ("document", kept)):
                for f in rows:
                    counts[profile][f"{f.entity_type.value}/{f.source}"] += 1
            for f in plain:
                if f.entity_type.value not in WATCHED:
                    continue
                findings.append(
                    {
                        "tenant": chunk.tenant,
                        "document": chunk.title,
                        "chunk": str(chunk.chunk_id),
                        "entity": f.entity_type.value,
                        "source": f.source,
                        "text": f.text,
                        "context": chunk.content[max(0, f.start - 40) : f.end + 40].replace("\n", " "),
                        "kept_in_document_profile": (f.start, f.end, f.entity_type.value) in kept_spans,
                    }
                )
        result = {
            "date": stamp(),
            "commit": git_commit(),
            "corpus_fingerprint": corpus.fingerprint,
            "chunks": len(corpus.chunks),
            "counts": {k: dict(sorted(v.items())) for k, v in counts.items()},
            "watched_findings": findings,
        }
    finally:
        await runtime.aclose()
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{result['date']}-pii-precision.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    result = asyncio.run(run())
    sys.stdout.write(json.dumps(result["counts"], ensure_ascii=False) + "\n")
    for f in result["watched_findings"]:
        mark = "keep" if f["kept_in_document_profile"] else "drop"
        sys.stdout.write(f"{mark} {f['entity']:7} {f['source']:12} {f['text']!r:40} | {f['context']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
