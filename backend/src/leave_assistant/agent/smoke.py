"""Live LLM smoke (`make chat-smoke`): S01–S05 (M6) or, with `--business`, the M7 business flow — S10 balance in Greek,
the S12 headline (balance → card → confirm), the manager approving it in their own chat — through the real
ChatService, gateway, services and providers inside the api container. `--order mistral` (or `groq`) forces one
provider; `--gemini-timeout 0.01` forces a transient Gemini failure → failover. Fictional seed data only
(ADR-0029). Conversations are archived; the request the business flow creates is cancelled at the end."""

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


async def _main(order: str | None, gemini_timeout: float | None, business: bool = False) -> int:
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
        if business:
            return 1 if await _business(services) else 0
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


async def _turn(services: Any, principal: Any, conversation_id: UUID, stream: Any) -> dict[str, Any]:
    started = time.monotonic()
    out: dict[str, Any] = {"text": "", "tools": [], "card": None, "outcome": None, "usage": {}, "error": None}
    async for event in stream:
        if event.name == "token":
            out["text"] += str(event.data["text"])
        elif event.name == "tool_call" and event.data["status"] != "started":
            out["tools"].append(f"{event.data['name']}:{event.data['status']}")
        elif event.name == "interrupt":
            out["card"], out["outcome"] = event.data["card"], "interrupted"
        elif event.name == "done":
            out["outcome"], out["usage"] = str(event.data["outcome"]), dict(event.data.get("usage") or {})
        elif event.name == "error":
            out["error"] = event.data
    out["seconds"] = time.monotonic() - started
    async with services.uow_factory(TenantContext.for_principal(principal)) as uow:
        runs = await _runs(uow, conversation_id)
    out["run"] = runs[-1] if runs else {}
    return out


def _report(
    label: str, question: str, result: dict[str, Any], language: str | None, expected: str | None
) -> None:
    run = result.get("run") or {}
    summary = (result.get("card") or {}).get("summary", {})
    card = {
        k: summary.get(k)
        for k in (
            "start_date",
            "end_date",
            "working_days",
            "available_before",
            "available_after",
            "approval_chain",
            "request_number",
            "requester",
        )
        if k in summary
    }  # noqa: E501
    sys.stdout.write(
        f"--- {label} {result['seconds']:.1f}s outcome={result['outcome']} provider={run.get('provider')} "
        f"model={run.get('model')} tokens={run.get('tokens')} failovers={run.get('failovers')} "
        f"language={language}{'' if expected is None else (' OK' if language == expected else ' MISMATCH')} "
        f"error={result['error']}\n  Q: {question}\n  tools: {result['tools']}\n  A: {result['text'][:400]}\n"
        + (f"  card: {card}\n" if card else "")
    )


async def _business(services: Any) -> int:
    from leave_assistant.application.leave.commands import CancelRequest

    failures = 0

    async def principal_of(slug: str, email: str | None = None, employee_id: UUID | None = None) -> Any:
        async with services.uow_factory(
            TenantContext(tenant_id=None, user_id="smoke", platform_admin=True)
        ) as uow:
            tenant = await uow.tenants.get_by_slug(slug)
        async with services.uow_factory(
            TenantContext(tenant_id=tenant.id, user_id="smoke", roles=("system",))
        ) as uow:
            employee = (
                await uow.employees.get_by_email(email) if email else await uow.employees.get(employee_id)
            )
            department = await uow.departments.get(employee.department_id)
        return employee_principal(tenant, employee), department

    def language_of(text: str) -> str | None:
        return (
            services.adapters.language.detect(re.sub(r"\[\d+\]|LR-\d{4}-\d{6}", " ", text)) if text else None
        )

    # S10 — balance in Greek
    eleni, _ = await principal_of("aegean", "eleni.dimitriou@aegean-logistics.example")
    conversation = await services.chat.create(eleni)
    question = "Πόσες μέρες μου απομένουν;"
    result = await _turn(
        services,
        eleni,
        conversation.id,
        await services.chat.send(eleni, conversation.id, question, request_id="smoke-S10"),
    )  # noqa: E501
    _report("S10 (aegean)", question, result, language_of(result["text"]), "el")
    failures += int(result["error"] is not None or language_of(result["text"]) != "el")
    await services.chat.archive(eleni, conversation.id)

    # S12 — the headline, confirmed
    niamh, department = await principal_of("brightwater", "niamh.kelly@brightwater.example")
    conversation = await services.chat.create(niamh)
    question = "How many vacation days do I have remaining? Can I request five days off starting December 14?"
    result = await _turn(
        services,
        niamh,
        conversation.id,
        await services.chat.send(niamh, conversation.id, question, request_id="smoke-S12"),
    )  # noqa: E501
    _report("S12 (brightwater) prepare", question, result, language_of(result["text"]), "en")
    if result["card"] is None:
        return failures + 1
    card = result["card"]
    confirmed = await _turn(
        services,
        niamh,
        conversation.id,
        await services.chat.resume(
            niamh, conversation.id, proposal_id=UUID(card["proposal_id"]), decision="confirm",
            payload_hash=card["payload_hash"], edits=None, request_id="smoke-S12-confirm",
        ),
    )  # fmt: skip
    _report("S12 (brightwater) confirm", "[confirm]", confirmed, language_of(confirmed["text"]), "en")
    match = re.search(r"LR-\d{4}-\d{6}", confirmed["text"])
    failures += int(confirmed["outcome"] != "executed" or match is None)
    await services.chat.archive(niamh, conversation.id)
    if match is None:
        return failures

    # S16 — the department manager approves it in their own chat
    manager, _ = await principal_of("brightwater", employee_id=department.manager_employee_id)
    conversation = await services.chat.create(manager)
    question = f"What is waiting for my approval? Approve {match.group(0)}."
    result = await _turn(
        services,
        manager,
        conversation.id,
        await services.chat.send(manager, conversation.id, question, request_id="smoke-S16"),
    )  # noqa: E501
    _report("S16 (manager) prepare", question, result, language_of(result["text"]), "en")
    if result["card"] is not None:
        card = result["card"]
        approved = await _turn(
            services,
            manager,
            conversation.id,
            await services.chat.resume(
                manager, conversation.id, proposal_id=UUID(card["proposal_id"]), decision="confirm",
                payload_hash=card["payload_hash"], edits=None, request_id="smoke-S16-confirm",
            ),
        )  # fmt: skip
        _report("S16 (manager) confirm", "[confirm]", approved, language_of(approved["text"]), "en")
        failures += int(approved["outcome"] != "executed")
    else:
        failures += 1
    await services.chat.archive(manager, conversation.id)

    # clean-up: re-runs must not overlap the request this flow created
    async with services.uow_factory(TenantContext.for_principal(niamh)) as uow:
        request = await uow.leave_requests.get_by_number(match.group(0))
    if request is not None:
        cancelled = await services.leave_requests.cancel(
            niamh, request.id, CancelRequest(version=request.version)
        )
        sys.stdout.write(f"--- clean-up: {match.group(0)} → {cancelled.request.status.value}\n")
    return failures


async def _runs(uow: Any, conversation_id: UUID) -> list[Any]:
    from sqlalchemy import select

    from leave_assistant.infrastructure.db.models import AgentRunRow

    rows = (
        await uow.session.execute(select(AgentRunRow).where(AgentRunRow.conversation_id == conversation_id))
    ).scalars()
    return [
        {
            "failovers": r.failovers,
            "provider": r.provider_used,
            "model": r.model_used,
            "tokens": f"{r.input_tokens}/{r.output_tokens}",
            "details": dict(r.details),
        }
        for r in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Live LLM smoke (M6)")
    parser.add_argument("--order", help="override LLM_PROVIDER_ORDER, e.g. mistral")
    parser.add_argument(
        "--gemini-timeout", type=float, help="force a Gemini timeout (seconds) to exercise failover"
    )
    parser.add_argument("--business", action="store_true", help="run the M7 business flow instead of S01–S05")
    args = parser.parse_args()
    return asyncio.run(_main(args.order, args.gemini_timeout, args.business))


if __name__ == "__main__":
    raise SystemExit(main())
