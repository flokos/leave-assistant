"""RetentionService: policies (C), scan job → review items + HR notification, HR decisions, deletion job."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.documents.ingestion import worker_context
from leave_assistant.application.errors import ConflictError, InvalidInputError, NotFoundError
from leave_assistant.application.jobs.models import Job
from leave_assistant.application.jobs.service import JobService
from leave_assistant.application.notifications.models import IntegrationEvent
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.ports import (
    Clock,
    DocumentStorage,
    IdGenerator,
    RetentionPolicy,
    RetentionReviewItem,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.tenancy.commands import UNSET
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.lifecycle import VersionEvent, can, transition
from leave_assistant.domain.documents.types import VersionStatus
from leave_assistant.domain.retention.eligibility import (
    MAX_RETENTION_DAYS,
    NEVER_DELETED,
    DocumentClass,
    RetentionAction,
    ReviewStatus,
    eligible_on,
    is_eligible,
)
from leave_assistant.domain.tenancy import Principal, Role

JOB_RETENTION_SCAN = "retention.scan"
JOB_RETENTION_DELETE = "retention.delete"


@dataclass(frozen=True, slots=True)
class CreateRetentionPolicy:
    document_class: DocumentClass
    retention_days: int
    action: RetentionAction = RetentionAction.NOTIFY_ONLY
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class UpdateRetentionPolicy:
    retention_days: int = UNSET
    action: RetentionAction = UNSET
    enabled: bool = UNSET


@dataclass(frozen=True, slots=True)
class ScanReport:
    tenant_id: UUID
    created: int
    eligible: int


class RetentionService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        notifications: NotificationService,
        jobs: JobService,
        storage: DocumentStorage,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._notifications = notifications
        self._jobs = jobs
        self._storage = storage

    # --- policies (company administrators) ----------------------------------------------------------------
    async def list_policies(self, principal: Principal) -> builtins.list[RetentionPolicy]:
        require(principal, Action.READ_AUDIT_TENANT)  # HR and company administrators may read
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.retention_policies.list()

    @staticmethod
    def _validate(document_class: DocumentClass, retention_days: int) -> None:
        if document_class in NEVER_DELETED:
            raise InvalidInputError(
                f"{document_class.value} records are never deleted",
                errors=[{"loc": ["body", "document_class"], "msg": "never deleted"}],
            )
        if not 0 <= retention_days <= MAX_RETENTION_DAYS:
            raise InvalidInputError(
                "retention_days out of range", errors=[{"loc": ["body", "retention_days"], "msg": "0–3650"}]
            )

    async def create_policy(self, principal: Principal, cmd: CreateRetentionPolicy) -> RetentionPolicy:
        require(principal, Action.MANAGE_ORGANISATION)
        assert principal.tenant_id is not None
        self._validate(cmd.document_class, cmd.retention_days)
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            if await uow.retention_policies.get_by_class(cmd.document_class) is not None:
                raise ConflictError(f"a policy for {cmd.document_class.value} already exists")
            policy = RetentionPolicy(
                id=self._ids.new_id(),
                tenant_id=principal.tenant_id,
                document_class=cmd.document_class,
                retention_days=cmd.retention_days,
                action=cmd.action,
                enabled=cmd.enabled,
                updated_by=principal.user_id,
                created_at=now,
                updated_at=now,
            )
            await uow.retention_policies.add(policy)
            await self._audit.record(
                uow,
                principal,
                "admin.config.changed",
                resource_type="retention_policy",
                resource_id=policy.id,
                details={
                    "created": cmd.document_class.value,
                    "retention_days": cmd.retention_days,
                    "action": cmd.action.value,
                },
            )
            await uow.commit()
        return policy

    async def update_policy(
        self, principal: Principal, policy_id: UUID, cmd: UpdateRetentionPolicy
    ) -> RetentionPolicy:
        require(principal, Action.MANAGE_ORGANISATION)
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            policy = await uow.retention_policies.get(policy_id)
            if policy is None:
                raise NotFoundError("retention policy not found")
            changes: dict[str, object] = {}
            if cmd.retention_days is not UNSET:
                self._validate(policy.document_class, cmd.retention_days)
                policy.retention_days = cmd.retention_days
                changes["retention_days"] = cmd.retention_days
            if cmd.action is not UNSET:
                policy.action = cmd.action
                changes["action"] = cmd.action.value
            if cmd.enabled is not UNSET:
                policy.enabled = cmd.enabled
                changes["enabled"] = cmd.enabled
            policy.updated_by = principal.user_id
            policy.updated_at = now
            await uow.retention_policies.update(policy)
            await self._audit.record(
                uow,
                principal,
                "admin.config.changed",
                resource_type="retention_policy",
                resource_id=policy.id,
                details=changes,
            )
            await uow.commit()
        return policy

    async def delete_policy(self, principal: Principal, policy_id: UUID) -> None:
        require(principal, Action.MANAGE_ORGANISATION)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            policy = await uow.retention_policies.get(policy_id)
            if policy is None:
                raise NotFoundError("retention policy not found")
            await uow.retention_policies.delete(policy_id)
            await self._audit.record(
                uow,
                principal,
                "admin.config.changed",
                resource_type="retention_policy",
                resource_id=policy_id,
                details={"deleted": policy.document_class.value},
            )
            await uow.commit()

    # --- scan (worker, per tenant) ---------------------------------------------------------------------------
    async def scan_tenant(self, tenant_id: UUID) -> ScanReport:
        today = self._clock.now().date()
        created = eligible = 0
        async with self._uow(worker_context(tenant_id)) as uow:
            for policy in await uow.retention_policies.list():
                if not policy.enabled or policy.document_class in NEVER_DELETED:
                    continue
                for subject_type, subject_id, label, reference in await self._candidates(
                    uow, policy.document_class
                ):
                    if not is_eligible(reference, policy.retention_days, today):
                        continue
                    eligible += 1
                    latest = await uow.retention_items.latest_for(subject_type, subject_id)
                    if latest is not None and not self._may_reopen(latest, policy, today):
                        continue
                    await uow.retention_items.add(
                        RetentionReviewItem(
                            id=self._ids.new_id(),
                            tenant_id=tenant_id,
                            policy_id=policy.id,
                            subject_type=subject_type,
                            subject_id=subject_id,
                            label=label,
                            eligible_since=eligible_on(reference, policy.retention_days),
                            status=ReviewStatus.PENDING,
                            created_at=self._clock.now(),
                        )
                    )
                    created += 1
            await self._audit.record(
                uow,
                None,
                "retention.scan",
                resource_type="tenant",
                resource_id=tenant_id,
                details={"eligible": eligible, "created": created},
            )
            if created:
                await uow.integration_events.add(
                    IntegrationEvent(
                        id=self._ids.new_id(),
                        tenant_id=tenant_id,
                        type="retention.items_found",
                        payload={"tenant_id": str(tenant_id), "created": created},
                        created_at=self._clock.now(),
                    )
                )
                hr_admins = await uow.employees.list_active(role=Role.HR_ADMIN)
                await self._notifications.notify(
                    uow,
                    [e.id for e in hr_admins],
                    "retention.items_found",
                    {"created": created},
                    dedupe_key=f"retention.items_found:{tenant_id}:{today.isoformat()}",
                )
            await uow.commit()
        return ScanReport(tenant_id=tenant_id, created=created, eligible=eligible)

    @staticmethod
    def _may_reopen(latest: RetentionReviewItem, policy: RetentionPolicy, today: date) -> bool:
        """A retained subject is asked about again after another retention period, not on every daily scan."""
        if latest.status is not ReviewStatus.RETAINED or latest.decided_at is None:
            return False
        return is_eligible(latest.decided_at.date(), policy.retention_days, today)

    async def _candidates(
        self, uow: UnitOfWork, document_class: DocumentClass
    ) -> builtins.list[tuple[str, UUID, str, date]]:
        """`(subject_type, subject_id, label, reference_date)` per class (doc 07 §7)."""
        out: builtins.list[tuple[str, UUID, str, date]] = []
        if document_class is DocumentClass.SUPPORTING_DOCUMENT:
            for document in await uow.supporting_documents.list_by_status("stored"):
                request = await uow.leave_requests.get(document.request_id)
                if request is None:
                    continue
                out.append(
                    (
                        "supporting_document",
                        document.id,
                        f"{request.request_number} · {document.original_filename}",
                        request.end_date,
                    )
                )
        elif document_class is DocumentClass.COMPANY_DOCUMENT:
            for version in await uow.document_versions.list_by_status(
                frozenset({VersionStatus.SUPERSEDED, VersionStatus.RETIRED})
            ):
                company_document = await uow.documents.get(version.document_id)
                title = company_document.title if company_document else str(version.document_id)
                out.append(
                    (
                        "document_version",
                        version.id,
                        f"{title} v{version.version_no}",
                        version.updated_at.date(),
                    )
                )
        return out

    async def scan_all(self) -> builtins.list[ScanReport]:
        async with self._uow(TenantContext(tenant_id=None, user_id="retention", platform_admin=True)) as uow:
            tenants = await uow.tenants.list(limit=200, after=None)
        return [await self.scan_tenant(tenant.id) for tenant in tenants]

    # --- HR review -------------------------------------------------------------------------------------------
    async def list_items(
        self, principal: Principal, *, status: ReviewStatus | None
    ) -> builtins.list[RetentionReviewItem]:
        require(principal, Action.RETENTION_REVIEW)
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            return await uow.retention_items.list(status=status)

    async def decide(
        self, principal: Principal, item_id: UUID, decision: ReviewStatus, comment: str | None
    ) -> RetentionReviewItem:
        require(principal, Action.RETENTION_REVIEW)
        if decision not in (ReviewStatus.APPROVED_DELETE, ReviewStatus.RETAINED):
            raise InvalidInputError(
                "decision must be approved_delete or retained",
                errors=[{"loc": ["body", "decision"], "msg": "approved_delete | retained"}],
                problem_type="retention/invalid-decision",
            )
        now = self._clock.now()
        async with self._uow(TenantContext.for_principal(principal)) as uow:
            item = await uow.retention_items.get(item_id)
            if item is None:
                raise NotFoundError("review item not found")
            if item.status is not ReviewStatus.PENDING:
                raise ConflictError("the item was already decided", problem_type="retention/invalid-decision")
            item.status = decision
            item.reviewer_employee_id = principal.employee_id
            item.decided_at = now
            item.comment = " ".join(comment.split()) if comment else None
            await uow.retention_items.update(item)
            await self._audit.record(
                uow,
                principal,
                "retention.decision",
                resource_type=item.subject_type,
                resource_id=item.subject_id,
                details={"item_id": str(item.id), "decision": decision.value, "comment": item.comment},
            )
            if decision is ReviewStatus.APPROVED_DELETE:
                await self._jobs.enqueue(
                    uow,
                    type=JOB_RETENTION_DELETE,
                    payload={"tenant_id": str(item.tenant_id), "item_id": str(item.id)},
                    tenant_id=item.tenant_id,
                    dedupe_key=f"{JOB_RETENTION_DELETE}:{item.id}",
                )
            await uow.commit()
        return item

    # --- deletion (worker) -----------------------------------------------------------------------------------
    async def execute_deletion(self, tenant_id: UUID, item_id: UUID) -> RetentionReviewItem:
        now = self._clock.now()
        keys: list[str] = []
        async with self._uow(worker_context(tenant_id)) as uow:
            item = await uow.retention_items.get(item_id)
            if item is None:
                raise NotFoundError("review item not found")
            if item.status is ReviewStatus.DELETED:
                return item
            if item.status is not ReviewStatus.APPROVED_DELETE:
                raise ConflictError("deletion was not approved", problem_type="retention/invalid-decision")
            if item.subject_type == "supporting_document":
                document = await uow.supporting_documents.get(item.subject_id)
                if document is not None and document.status != "deleted":
                    keys.append(document.object_key)
                    document.status = "deleted"
                    document.deleted_at = now
                    await uow.supporting_documents.update(document)
            else:
                version = await uow.document_versions.get(item.subject_id)
                if version is not None and version.status is not VersionStatus.DELETED:
                    keys.extend(k for k in (version.object_key, version.extracted_object_key) if k)
                    await uow.chunks.replace_for_version(version.id, [])
                    if can(version.status, VersionEvent.DELETE):
                        version.status = transition(version.status, VersionEvent.DELETE)
                    version.retrievable = False
                    version.updated_at = now
                    await uow.document_versions.update(version)
            # objects first: a storage failure fails the job (retried) and nothing is recorded as deleted
            for key in keys:
                await self._storage.delete(key)
            item.status = ReviewStatus.DELETED
            await uow.retention_items.update(item)
            await self._audit.record(
                uow,
                None,
                "retention.deleted",
                resource_type=item.subject_type,
                resource_id=item.subject_id,
                details={"item_id": str(item.id), "objects": keys},
            )
            await uow.commit()
        return item

    # --- job handlers ---------------------------------------------------------------------------------------
    async def handle_scan_job(self, job: Job) -> object:
        tenant_id = job.payload.get("tenant_id")
        if tenant_id:
            return await self.scan_tenant(UUID(str(tenant_id)))
        return await self.scan_all()

    async def handle_delete_job(self, job: Job) -> object:
        return await self.execute_deletion(
            UUID(str(job.payload["tenant_id"])), UUID(str(job.payload["item_id"]))
        )
