"""SupportingDocumentService: access matrix, formats, envelope encryption, tokens, access events, audit."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from leave_assistant.application.container import Services
from leave_assistant.application.errors import (
    AuthorizationError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    SecurityNotConfigured,
)
from leave_assistant.application.leave.commands import Decide, SubmitRequest
from leave_assistant.domain.documents.formats import TooLarge, UnsupportedType
from leave_assistant.domain.tenancy import Principal
from tests.conftest import fake_document_adapters, principal_for
from tests.fakes.memory import MemoryStore
from tests.fakes.system import FixedClock
from tests.fakes.world import World
from tests.unit.documents.conftest import make_upload

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%fake medical certificate\n" + b"x" * 32
NOV_9, NOV_10 = date(2026, 11, 9), date(2026, 11, 10)


async def sick_request(services: Services, employee: Principal):  # type: ignore[no-untyped-def]
    detail = await services.leave_requests.submit(
        employee, SubmitRequest("sick", NOV_9, NOV_10, reason="flu")
    )
    return detail.request


async def test_owner_uploads_hr_reads_and_downloads_with_reason_managers_see_nothing(
    services: Services, leave_world: World, store: MemoryStore, clock: FixedClock, tmp_path: Path
) -> None:
    world = leave_world
    employee = principal_for(world.a.employees["employee"])
    hr = principal_for(world.a.employees["hr_admin"])
    manager = principal_for(world.a.employees["manager"])
    admin = principal_for(world.a.employees["company_admin"])
    colleague = principal_for(world.a.employees["employee2"])
    request = await sick_request(services, employee)

    document = await services.supporting.upload(
        employee, request.id, make_upload(tmp_path, "note.png", PNG, declared="image/png")
    )
    assert (
        document.mime == "image/png"
        and document.status == "stored"
        and document.employee_id == employee.employee_id
    )
    assert document.object_key == f"tenants/{world.a.tenant.id}/supporting/{request.id}/{document.id}"
    stored_bytes = services.adapters.storage.objects[document.object_key][0]  # type: ignore[attr-defined]
    assert (
        stored_bytes != PNG and PNG[:8] not in stored_bytes and len(document.encrypted_dek) == 40
    )  # AES-KW of 32 bytes
    assert store.audit[-1].action == "supporting.uploaded" and store.audit[-1].details["on_behalf"] is False
    assert [n.type for n in store.notifications.values() if n.type == "supporting.uploaded"] == [
        "supporting.uploaded"
    ]
    assert (
        next(n for n in store.notifications.values() if n.type == "supporting.uploaded").recipient_employee_id
        == hr.employee_id
    )

    # metadata: owner and HR; everybody else 404
    assert [d.id for d in await services.supporting.list_for_request(employee, request.id)] == [document.id]
    assert [d.id for d in await services.supporting.list_for_request(hr, request.id)] == [document.id]
    for other in (manager, admin, colleague):
        with pytest.raises(NotFoundError):
            await services.supporting.list_for_request(other, request.id)
        with pytest.raises(NotFoundError):
            await services.supporting.get(other, document.id)
        with pytest.raises(NotFoundError):
            await services.supporting.issue_download_token(other, document.id, "curious")
    owner_view = await services.supporting.get(employee, document.id)
    assert owner_view.access_events == []  # the owner does not see the access history
    hr_view = await services.supporting.get(hr, document.id)
    assert [e.action for e in hr_view.access_events] == ["view", "view"]

    # HR needs a reason; the owner does not
    with pytest.raises(InvalidInputError):
        await services.supporting.issue_download_token(hr, document.id, None)
    issued = await services.supporting.issue_download_token(hr, document.id, "verifying the certificate")
    assert issued.expires_at == clock.now() + timedelta(seconds=60)
    downloaded, plaintext = await services.supporting.download(issued.token)
    assert downloaded.id == document.id and plaintext == PNG
    with pytest.raises(NotFoundError):  # single use
        await services.supporting.download(issued.token)
    owner_token = await services.supporting.issue_download_token(employee, document.id, None)
    clock.tick(61)
    with pytest.raises(NotFoundError):  # expired
        await services.supporting.download(owner_token.token)
    events = [(e.actor_employee_id, e.action, e.reason) for e in store.supporting_access]
    assert (hr.employee_id, "download_token", "verifying the certificate") in events
    assert (hr.employee_id, "download", "verifying the certificate") in events
    actions = [a.action for a in store.audit if a.action.startswith("supporting.")]
    assert {
        "supporting.uploaded",
        "supporting.view",
        "supporting.download_token",
        "supporting.download",
    } <= set(actions)
    assert all("note.png" not in str(a.details) or a.action == "supporting.uploaded" for a in store.audit)

    # request detail: owner and HR see the count; the manager does not
    assert (await services.leave_requests.get(employee, request.id)).supporting_count == 1
    assert (await services.leave_requests.get(hr, request.id)).supporting_count == 1
    assert (await services.leave_requests.get(manager, request.id)).supporting_count == 0

    # delete: HR only, reason required, tombstone keeps the metadata
    with pytest.raises(AuthorizationError):
        await services.supporting.delete(employee, document.id, "mine")
    with pytest.raises(InvalidInputError):
        await services.supporting.delete(hr, document.id, "   ")
    deleted = await services.supporting.delete(hr, document.id, "duplicate upload")
    assert (
        deleted.status == "deleted"
        and deleted.deleted_at == clock.now()
        and deleted.sha256 == document.sha256
    )
    assert document.object_key not in services.adapters.storage.objects  # type: ignore[attr-defined]
    assert await services.supporting.list_for_request(hr, request.id) == []
    with pytest.raises(NotFoundError):
        await services.supporting.get(hr, document.id)


async def test_hr_may_attach_on_behalf_and_formats_sizes_and_states_are_enforced(
    services: Services, leave_world: World, store: MemoryStore, tmp_path: Path
) -> None:
    world = leave_world
    employee = principal_for(world.a.employees["employee"])
    hr = principal_for(world.a.employees["hr_admin"])
    request = await sick_request(services, employee)
    on_behalf = await services.supporting.upload(hr, request.id, make_upload(tmp_path, "cert.pdf", PDF))
    assert (
        on_behalf.employee_id == employee.employee_id and on_behalf.uploaded_by_employee_id == hr.employee_id
    )
    assert on_behalf.original_filename == "cert.pdf" and store.audit[-1].details["on_behalf"] is True
    with pytest.raises(UnsupportedType) as unsupported:
        await services.supporting.upload(
            employee, request.id, make_upload(tmp_path, "x.docx", b"PK\x03\x04" + b"\x00" * 30)
        )
    assert unsupported.value.code == "supporting/unsupported-type"
    with pytest.raises(UnsupportedType):
        await services.supporting.upload(employee, request.id, make_upload(tmp_path, "empty.png", b""))
    big = make_upload(tmp_path, "big.png", PNG + b"\x00" * (10 * 1024 * 1024))
    with pytest.raises(TooLarge):
        await services.supporting.upload(employee, request.id, big)
    # closed requests refuse attachments
    steps = await services.approvals.pending(hr)
    if steps:
        await services.approvals.reject(
            hr, steps[0].step.id, Decide(version=steps[0].step.version, comment="no")
        )
        with pytest.raises(ConflictError):
            await services.supporting.upload(employee, request.id, make_upload(tmp_path, "late.png", PNG))
    # cross-tenant: an HR administrator of tenant B cannot see A's request at all
    with pytest.raises(NotFoundError):
        await services.supporting.list_for_request(principal_for(world.b.employees["hr_admin"]), request.id)


async def test_uploads_fail_closed_without_the_app_kek(
    leave_world: World, store: MemoryStore, identity, clock, ids, test_keys, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    from leave_assistant.application.container import DocumentOptions, Services
    from leave_assistant.infrastructure.identity.tokens import TokenVerifier
    from tests.fakes.memory import MemoryUnitOfWorkFactory
    from tests.fakes.tokens import AUDIENCE, ISSUER

    services = Services.build(
        uow_factory=MemoryUnitOfWorkFactory(store),
        identity=identity,
        verifier=TokenVerifier(test_keys.resolver(), issuer=ISSUER, audience=AUDIENCE, leeway_seconds=30),
        clock=clock,
        ids=ids,
        adapters=fake_document_adapters(keys_configured=False),
        options=DocumentOptions(),
    )
    employee = principal_for(leave_world.a.employees["employee"])
    request = await sick_request(services, employee)
    with pytest.raises(SecurityNotConfigured):
        await services.supporting.upload(employee, request.id, make_upload(tmp_path, "note.png", PNG))
    assert not store.supporting_documents
