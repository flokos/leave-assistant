"""Live LLM smoke (`make chat-smoke`, M6): S01/S02/S05 through the real ChatService, retrieval and providers inside
the api container. `--order mistral` (or `groq`) forces one provider; `--gemini-timeout 0.01` forces a transient Gemini failure → failover.
Fictional seed data only (ADR-0029). Conversations are archived afterwards."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from typing import Any
from uuid import UUID

from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.config import get_settings
from leave_assistant.infrastructure.observability.logging import configure_logging
from leave_assistant.infrastructure.runtime import build_runtime
from leave_assistant.seed.run import employee_principal

CASES = (  # (id, tenant, user, question, expected answer language)
    (
        "S01",
        "aegean",
        "eleni.dimitriou@aegean-logistics.example",
        "Πόσες μέρες άδειας δικαιούμαι τον χρόνο;",
        "el",
    ),
    (
        "S02",
        "brightwater",
        "niamh.kelly@brightwater.example",
        "How many days of annual leave do I get?",
        "en",
    ),
    (
        "S03",
        "brightwater",
        "niamh.kelly@brightwater.example",
        "Μεταφέρονται οι μέρες που δεν πήρα στο επόμενο έτος;",
        "el",
    ),
    (
        "S04",
        "aegean",
        "eleni.dimitriou@aegean-logistics.example",
        "Can I carry over unused days to next year?",
        "en",
    ),
    ("S05", "aegean", "eleni.dimitriou@aegean-logistics.example", "Πληρώνεται η άδεια γάμου;", "el"),
)


async def _main(order: str | None, gemini_timeout: float | None) -> int:
    updates: dict[str, object] = {}
    if order:
        updates["llm_provider_order"] = order
    if gemini_timeout is not None:
        updates["gemini_timeout_seconds"] = gemini_timeout
    settings = get_settings().model_copy(update=updates)
    configure_logging(settings)
    runtime = build_runtime(settings, role="api")
    failures = 0
    try:
        if not await runtime.start_agent():
            sys.stdout.write("no provider configured (GEMINI_API_KEY / GROQ_API_KEY)\n")
            return 2
        services = runtime.services
        for case, slug, email, question, expected_language in CASES:
            async with services.uow_factory(
                TenantContext(tenant_id=None, user_id="smoke", platform_admin=True)
            ) as uow:
                tenant = await uow.tenants.get_by_slug(slug)
            assert tenant is not None
            async with services.uow_factory(
                TenantContext(tenant_id=tenant.id, user_id="smoke", roles=("system",))
            ) as uow:
                employee = await uow.employees.get_by_email(email)
            if employee is None:
                sys.stdout.write(f"{case}: seed user {email} missing\n")
                failures += 1
                continue
            principal = employee_principal(tenant, employee)
            conversation = await services.chat.create(principal)
            started = time.monotonic()
            text, outcome, citations, usage, error = "", "?", [], {}, None
            async for event in await services.chat.send(
                principal, conversation.id, question, request_id=f"smoke-{case}"
            ):
                if event.name == "token":
                    text += str(event.data["text"])
                elif event.name == "citations":
                    citations = [f"[{c['n']}] {c['title']}" for c in event.data["items"]]
                elif event.name == "done":
                    outcome, usage = str(event.data["outcome"]), dict(event.data["usage"])
                elif event.name == "error":
                    error = event.data
            async with services.uow_factory(TenantContext.for_principal(principal)) as uow:
                runs = [r for r in await _runs(uow, conversation.id)]
            run = runs[-1] if runs else None
            seconds = time.monotonic() - started
            written = services.adapters.language.detect(re.sub(r"\[\d+\]", " ", text)) if text else None
            language_ok = written == expected_language
            failures += 0 if language_ok else 1
            sys.stdout.write(
                f"--- {case} ({slug}) {seconds:.1f}s outcome={outcome} provider={usage.get('provider')} "
                f"model={usage.get('model')} tokens={usage.get('input_tokens')}/{usage.get('output_tokens')} "
                f"failovers={run['failovers'] if run else '?'} language={written} (expected {expected_language}"
                f"{', OK' if language_ok else ', MISMATCH'}) error={error}\n  Q: {question}\n  A: {text[:400]}\n"
                f"  citations: {citations}\n"
            )
            failures += 0 if error is None else 1
            await services.chat.archive(principal, conversation.id)
        return 1 if failures else 0
    finally:
        await runtime.aclose()


async def _runs(uow: Any, conversation_id: UUID) -> list[Any]:
    from sqlalchemy import select

    from leave_assistant.infrastructure.db.models import AgentRunRow

    rows = (
        await uow.session.execute(select(AgentRunRow).where(AgentRunRow.conversation_id == conversation_id))
    ).scalars()
    return [{"failovers": r.failovers, "provider": r.provider_used, "details": dict(r.details)} for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Live LLM smoke (M6)")
    parser.add_argument("--order", help="override LLM_PROVIDER_ORDER, e.g. mistral")
    parser.add_argument(
        "--gemini-timeout", type=float, help="force a Gemini timeout (seconds) to exercise failover"
    )
    args = parser.parse_args()
    return asyncio.run(_main(args.order, args.gemini_timeout))


if __name__ == "__main__":
    raise SystemExit(main())
