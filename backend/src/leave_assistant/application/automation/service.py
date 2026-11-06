"""InternalOpsService: what the `/api/v1/internal/*` endpoints do for n8n (doc 06 §5, M8 decisions 6–10).

The automation principal has no tenant. Every operation either iterates tenants with explicit per-tenant system
contexts, or acts on identifiers carried by an outbox event under that event's tenant. Responses carry ids, counts
and statuses — never document content, request reasons, ledgers or vault data. The single exception is the e-mail
render for a notification, which goes to its authorised recipient (doc 06 W4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from leave_assistant.application.assistant.proposals import ProposalService
from leave_assistant.application.audit.entry import ActorKind
from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.authz.policy import Action, require
from leave_assistant.application.calendar.reconcile import ReconciliationService
from leave_assistant.application.calendar.sync import CalendarSyncService
from leave_assistant.application.chat.service import ChatService
from leave_assistant.application.errors import InvalidInputError, NotFoundError
from leave_assistant.application.jobs.models import JOB_DOCUMENT_REINDEX, JobStatus
from leave_assistant.application.jobs.service import JobService
from leave_assistant.application.notifications.models import EmailStatus, IntegrationEvent
from leave_assistant.application.notifications.render import render
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.ports import Clock, IdGenerator, JobFilter, UnitOfWorkFactory
from leave_assistant.application.retention.service import JOB_RETENTION_SCAN
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.calendar.model import SyncStatus
from leave_assistant.domain.documents.types import Category, VersionStatus
from leave_assistant.domain.retention.eligibility import ReviewStatus
from leave_assistant.domain.tenancy import Principal, Role, Tenant

SYSTEM_USER = "automation"
MAINTENANCE_TASKS = (
    "expired_proposals",
    "conversation_cleanup",
    "events_archive",
    "jobs_archive",
    "oauth_states",
)
REMINDER_TYPES = ("document.processing_stalled", "document.review_reminder", "retention.review_reminder")
PROCESSING = frozenset({VersionStatus.UPLOADED, VersionStatus.PROCESSING})
REVIEWERS = {  # who reviews a category (doc 05 §2, doc 08 §5.2)
    Category.POLICY_HR_RESTRICTED: Role.HR_ADMIN,
    Category.POLICY_GENERAL: Role.COMPANY_ADMIN,
    Category.DEPARTMENT_NOTICE: Role.COMPANY_ADMIN,
    Category.ADMIN_INTERNAL: Role.COMPANY_ADMIN,
}
FAILOVER_THRESHOLD = 3  # per provider per hour (doc 06 W7)


@dataclass(frozen=True, slots=True)
class OpsOptions:
    public_base_url: str = "http://localhost:8080"
    mail_from_domain: str = "leave.local"
    platform_alert_email: str = "platform-alerts@leave.local"
    lease_seconds: float = 300.0
    max_attempts: int = 10
    providers: tuple[str, ...] = ()
    stalled_after_minutes: int = 30
    delivered_retention_days: int = 7
    jobs_retention_days: int = 30


@dataclass(slots=True)
class EmailRender:
    notification_id: UUID
    tenant_id: UUID
    type: str
    send: bool  # e-mail is a channel and not already sent
    status: str
    to: str | None = None
    from_address: str | None = None
    subject: str | None = None
    text: str | None = None
    html: str | None = None


@dataclass(slots=True)
class ProviderHealth:
    healthy: bool
    window_minutes: int
    providers: list[dict[str, Any]] = field(default_factory=list)
    alert: dict[str, str] | None = None


def system(tenant_id: UUID) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, user_id=SYSTEM_USER, roles=("system",))


class InternalOpsService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        notifications: NotificationService,
        jobs: JobService,
        calendar_sync: CalendarSyncService,
        calendar_reconcile: ReconciliationService,
        proposals: ProposalService,
        chat: ChatService,
        *,
        index_generation: str,
        options: OpsOptions | None = None,
    ) -> None:
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._notifications = notifications
        self._jobs = jobs
        self._calendar = calendar_sync
        self._reconcile = calendar_reconcile
        self._proposals = proposals
        self._chat = chat
        self._generation = index_generation
        self.options = options or OpsOptions()

    @staticmethod
    def _require(principal: Principal) -> None:
        require(principal, Action.INTERNAL_CALL)

    async def _tenants(self) -> list[Tenant]:
        ctx = TenantContext(tenant_id=None, user_id=SYSTEM_USER, platform_admin=True)
        async with self._uow(ctx) as uow:
            return await uow.tenants.list(limit=500, after=None)

    async def _tenant(self, tenant_id: UUID) -> Tenant:
        for tenant in await self._tenants():
            if tenant.id == tenant_id:
                return tenant
        raise NotFoundError("tenant not found")

    # -- outbox (decision 8: lease instead of cursor) -----------------------------------------------------------
    async def lease_events(self, principal: Principal, *, limit: int = 50) -> list[IntegrationEvent]:
        self._require(principal)
        limit = min(max(limit, 1), 200)
        now = self._clock.now()
        until = now + timedelta(seconds=self.options.lease_seconds)
        leased: list[IntegrationEvent] = []
        for tenant in await self._tenants():
            if len(leased) >= limit:
                break
            async with self._uow(system(tenant.id)) as uow:
                leased.extend(
                    await uow.integration_events.lease(
                        now=now,
                        until=until,
                        limit=limit - len(leased),
                        max_attempts=self.options.max_attempts,
                    )
                )
                await uow.commit()
        return sorted(leased, key=lambda e: e.id)

    async def ack_events(self, principal: Principal, ids: list[UUID], *, dead: bool = False) -> int:
        self._require(principal)
        done = 0
        now = self._clock.now()
        for tenant in await self._tenants():
            async with self._uow(system(tenant.id)) as uow:
                repo = uow.integration_events
                done += await (repo.mark_dead(ids) if dead else repo.ack(ids, now))
                await uow.commit()
            if done >= len(set(ids)):
                break
        return done

    # -- documents ----------------------------------------------------------------------------------------------
    async def processing_status(
        self, principal: Principal, tenant_id: UUID, version_id: UUID
    ) -> dict[str, Any]:
        self._require(principal)
        async with self._uow(system(tenant_id)) as uow:
            version = await uow.document_versions.get(version_id)
        if version is None:
            raise NotFoundError("document version not found")
        minutes = int((self._clock.now() - version.created_at).total_seconds() // 60)
        return {
            "version_id": str(version.id),
            "tenant_id": str(tenant_id),
            "status": version.status.value,
            "processing": version.status in PROCESSING,
            "failed": version.status is VersionStatus.FAILED,
            "attempts": version.attempts,
            "age_minutes": minutes,
            "stalled": version.status in PROCESSING and minutes >= self.options.stalled_after_minutes,
        }

    async def pending_review(
        self, principal: Principal, *, older_than_hours: float = 48
    ) -> list[dict[str, Any]]:
        self._require(principal)
        cutoff = self._clock.now() - timedelta(hours=older_than_hours)
        out: list[dict[str, Any]] = []
        for tenant in await self._tenants():
            counts = await self._waiting_reviews(tenant.id, cutoff)
            if counts:
                out.append({"tenant_id": str(tenant.id), "count": sum(counts.values())})
        return out

    async def _waiting_reviews(self, tenant_id: UUID, cutoff: datetime) -> dict[Role, int]:
        counts: dict[Role, int] = {}
        async with self._uow(system(tenant_id)) as uow:
            for version in await uow.document_versions.list_by_status(
                frozenset({VersionStatus.PENDING_REVIEW, VersionStatus.QUARANTINED})
            ):
                if version.updated_at > cutoff:
                    continue
                document = await uow.documents.get(version.document_id)
                role = (
                    REVIEWERS.get(document.category, Role.COMPANY_ADMIN) if document else Role.COMPANY_ADMIN
                )
                counts[role] = counts.get(role, 0) + 1
        return counts

    # -- index and retention ------------------------------------------------------------------------------------
    async def index_status(self, principal: Principal) -> list[dict[str, Any]]:
        self._require(principal)
        out: list[dict[str, Any]] = []
        for tenant in await self._tenants():
            async with self._uow(system(tenant.id)) as uow:
                active = await uow.document_versions.list_by_status(frozenset({VersionStatus.ACTIVE}))
                queued = await uow.jobs.list(
                    filters=JobFilter(
                        status=JobStatus.QUEUED, type=JOB_DOCUMENT_REINDEX, tenant_id=tenant.id
                    ),
                    limit=500,
                )
            stale = sum(1 for v in active if v.index_generation != self._generation)
            out.append(
                {
                    "tenant_id": str(tenant.id),
                    "generation": self._generation,
                    "active_versions": len(active),
                    "stale_versions": stale,
                    "stale_generation": stale > 0,
                    "reindex_queued": len(queued),
                }
            )
        return out

    async def reindex(self, principal: Principal, tenant_id: UUID | None) -> dict[str, int]:
        """Enqueue `document.reindex` for ACTIVE versions on an older generation (shared with `make reindex`)."""
        self._require(principal)
        return await self.enqueue_reindex(tenant_id)

    async def enqueue_reindex(self, tenant_id: UUID | None, *, force: bool = False) -> dict[str, int]:
        tenants = [t for t in await self._tenants() if tenant_id is None or t.id == tenant_id]
        enqueued = 0
        for tenant in tenants:
            async with self._uow(system(tenant.id)) as uow:
                for version in await uow.document_versions.list_by_status(frozenset({VersionStatus.ACTIVE})):
                    if not force and version.index_generation == self._generation:
                        continue
                    await self._jobs.enqueue(
                        uow,
                        type=JOB_DOCUMENT_REINDEX,
                        payload={
                            "tenant_id": str(tenant.id),
                            "document_id": str(version.document_id),
                            "version_id": str(version.id),
                            "rescan": False,
                        },
                        tenant_id=tenant.id,
                        dedupe_key=f"{JOB_DOCUMENT_REINDEX}:{version.id}",
                    )
                    enqueued += 1
                await uow.commit()
        return {"tenants": len(tenants), "enqueued": enqueued}

    async def retention_scan(self, principal: Principal) -> dict[str, int]:
        """One `retention.scan` job per tenant (the worker's daily schedule does the same; deduplicated)."""
        self._require(principal)
        enqueued = 0
        for tenant in await self._tenants():
            async with self._uow(system(tenant.id)) as uow:
                await self._jobs.enqueue(
                    uow,
                    type=JOB_RETENTION_SCAN,
                    payload={"tenant_id": str(tenant.id)},
                    tenant_id=tenant.id,
                    dedupe_key=f"{JOB_RETENTION_SCAN}:{tenant.id}",
                )
                await uow.commit()
            enqueued += 1
        return {"enqueued": enqueued}

    async def retention_pending(
        self, principal: Principal, *, older_than_days: float = 7
    ) -> list[dict[str, Any]]:
        self._require(principal)
        cutoff = self._clock.now() - timedelta(days=older_than_days)
        out: list[dict[str, Any]] = []
        for tenant in await self._tenants():
            n = await self._retention_waiting(tenant.id, cutoff)
            if n:
                out.append({"tenant_id": str(tenant.id), "count": n})
        return out

    async def _retention_waiting(self, tenant_id: UUID, cutoff: datetime) -> int:
        async with self._uow(system(tenant_id)) as uow:
            items = await uow.retention_items.list(status=ReviewStatus.PENDING)
        return sum(1 for i in items if i.created_at <= cutoff)

    # -- notifications ------------------------------------------------------------------------------------------
    async def create_reminder(
        self,
        principal: Principal,
        *,
        tenant_id: UUID,
        type: str,
        subject_id: UUID | None = None,
        older_than_hours: float | None = None,
    ) -> dict[str, Any]:
        """Watchdog and reminder notifications (W1, W3, W6). The backend picks the recipients and parameters;
        n8n only says which reminder is due. Deduplicated per subject/day, so repeated triggers are harmless."""
        self._require(principal)
        if type not in REMINDER_TYPES:
            raise InvalidInputError(
                "unsupported notification type", errors=[{"field": "type", "allowed": REMINDER_TYPES}]
            )
        await self._tenant(tenant_id)
        now = self._clock.now()
        day = now.date().isoformat()
        async with self._uow(system(tenant_id)) as uow:
            recipients: list[UUID] = []
            params: dict[str, Any] = {}
            dedupe = f"{type}:{day}"
            if type == "document.processing_stalled":
                if subject_id is None:
                    raise InvalidInputError("subject_id (version id) is required")
                version = await uow.document_versions.get(subject_id)
                if version is None:
                    raise NotFoundError("document version not found")
                if version.status not in PROCESSING and version.status is not VersionStatus.FAILED:
                    return {"created": 0, "reason": "not stalled"}
                document = await uow.documents.get(version.document_id)
                admins = [e.id for e in await uow.employees.list_active(role=Role.COMPANY_ADMIN)]
                recipients = [version.uploaded_by_employee_id, *admins]
                params = {
                    "version_id": str(version.id),
                    "title": document.title if document else "",
                    "version_no": version.version_no,
                    "minutes": self.options.stalled_after_minutes,
                }
                dedupe = f"{type}:{version.id}"
            elif type == "document.review_reminder":
                cutoff = now - timedelta(hours=older_than_hours or 48)
                for role, count in (await self._waiting_reviews(tenant_id, cutoff)).items():
                    for employee in await uow.employees.list_active(role=role):
                        recipients.append(employee.id)
                    params = {"count": params.get("count", 0) + count}
            else:
                cutoff = now - timedelta(hours=older_than_hours or 24 * 7)
                count = await self._retention_waiting(tenant_id, cutoff)
                if count:
                    recipients = [e.id for e in await uow.employees.list_active(role=Role.HR_ADMIN)]
                    params = {"count": count}
            if not recipients:
                return {"created": 0, "reason": "nothing to remind"}
            created = await self._notifications.notify(uow, recipients, type, params, dedupe_key=dedupe)
            await uow.commit()
        return {"created": len(created)}

    async def render_email(self, principal: Principal, tenant_id: UUID, notification_id: UUID) -> EmailRender:
        self._require(principal)
        tenant = await self._tenant(tenant_id)
        async with self._uow(system(tenant_id)) as uow:
            notification = await uow.notifications.get(notification_id)
            if notification is None:
                raise NotFoundError("notification not found")
            recipient = await uow.employees.get(notification.recipient_employee_id)
        result = EmailRender(
            notification_id=notification.id,
            tenant_id=tenant_id,
            type=notification.type,
            send=False,
            status=notification.email_status.value,
        )
        if "email" not in notification.channels or recipient is None or not recipient.email:
            return result
        if notification.email_status in (EmailStatus.SENT, EmailStatus.SKIPPED):
            return result
        email = render(
            notification.type,
            notification.params,
            recipient.locale.value,
            base_url=self.options.public_base_url,
        )
        if email is None:
            return result
        result.send = True
        result.to = recipient.email
        result.from_address = f"no-reply@{tenant.slug}.{self.options.mail_from_domain}"
        result.subject, result.text, result.html = email.subject, email.text, email.html
        return result

    async def record_delivery(
        self, principal: Principal, tenant_id: UUID, notification_id: UUID, status: str
    ) -> dict[str, Any]:
        """Idempotent by notification id: once `sent`, later reports change nothing."""
        self._require(principal)
        try:
            target = EmailStatus(status)
        except ValueError:
            raise InvalidInputError("status must be sent, failed or skipped") from None
        if target is EmailStatus.PENDING:
            raise InvalidInputError("status must be sent, failed or skipped")
        async with self._uow(system(tenant_id)) as uow:
            notification = await uow.notifications.get(notification_id)
            if notification is None:
                raise NotFoundError("notification not found")
            if notification.email_status is not EmailStatus.SENT:
                notification.email_status = target
                notification.email_attempts += 1
                await uow.notifications.update(notification)
                await uow.commit()
        return {"status": notification.email_status.value, "attempts": notification.email_attempts}

    # -- calendar -----------------------------------------------------------------------------------------------
    async def calendar_retry(self, principal: Principal) -> dict[str, int]:
        self._require(principal)
        requeued = 0
        for tenant in await self._tenants():
            requeued += await self._calendar.sweep(tenant.id)
        return {"requeued": requeued}

    async def calendar_reconcile(self, principal: Principal) -> dict[str, Any]:
        self._require(principal)
        return await self._reconcile.run_all()

    # -- housekeeping -------------------------------------------------------------------------------------------
    async def maintenance(self, principal: Principal, tasks: list[str] | None) -> dict[str, Any]:
        self._require(principal)
        wanted = list(tasks or MAINTENANCE_TASKS)
        unknown = [t for t in wanted if t not in MAINTENANCE_TASKS]
        if unknown:
            raise InvalidInputError(
                "unknown maintenance task", errors=[{"field": "tasks", "unknown": unknown}]
            )
        now = self._clock.now()
        results: dict[str, Any] = {}
        if "expired_proposals" in wanted:
            results["expired_proposals"] = await self._proposals.expire_due()
        if "conversation_cleanup" in wanted:
            results["conversation_cleanup"] = await self._chat.cleanup_expired()
        if "events_archive" in wanted:
            purged = 0
            for tenant in await self._tenants():
                async with self._uow(system(tenant.id)) as uow:
                    purged += await uow.integration_events.purge_delivered(
                        now - timedelta(days=self.options.delivered_retention_days)
                    )
                    await uow.commit()
            results["events_archive"] = {"deleted": purged}
        if "jobs_archive" in wanted:
            async with self._uow(None) as uow:
                deleted = await uow.jobs.purge_finished(
                    now - timedelta(days=self.options.jobs_retention_days)
                )
                await uow.commit()
            results["jobs_archive"] = {"deleted": deleted}
        if "oauth_states" in wanted:
            async with self._uow(None) as uow:
                deleted = await uow.oauth_states.purge(now - timedelta(days=1))
                await uow.commit()
            results["oauth_states"] = {"deleted": deleted}
        return results

    # -- health -------------------------------------------------------------------------------------------------
    async def provider_counts(self, *, window: timedelta) -> dict[str, dict[str, int]]:
        since = self._clock.now() - window
        totals: dict[str, dict[str, int]] = {p: {} for p in self.options.providers}
        for tenant in await self._tenants():
            async with self._uow(system(tenant.id)) as uow:
                for (provider, event), n in (await uow.provider_events.counts_since(since)).items():
                    bucket = totals.setdefault(provider, {})
                    bucket[event] = bucket.get(event, 0) + n
        return totals

    async def provider_health(self, principal: Principal, *, window_minutes: int = 60) -> ProviderHealth:
        self._require(principal)
        return await self.health_digest(window_minutes=window_minutes)

    async def health_digest(self, *, window_minutes: int = 60) -> ProviderHealth:
        counts = await self.provider_counts(window=timedelta(minutes=window_minutes))
        providers = []
        for name, events in sorted(counts.items()):
            failovers = events.get("failover", 0)
            opened = events.get("circuit_open", 0)
            healthy = failovers < FAILOVER_THRESHOLD and opened == 0
            providers.append(
                {
                    "provider": name,
                    "healthy": healthy,
                    "failovers": failovers,
                    "circuit_open": opened,
                    "errors": events.get("error", 0),
                }
            )
        healthy = all(p["healthy"] for p in providers)
        report = ProviderHealth(healthy=healthy, window_minutes=window_minutes, providers=providers)
        if not healthy:
            lines = [
                f"- {p['provider']}: {p['failovers']} failover(s), {p['circuit_open']} circuit opening(s), "
                f"{p['errors']} error(s)"
                for p in providers
                if not p["healthy"]
            ]
            report.alert = {
                "to": self.options.platform_alert_email,
                "from": f"no-reply@platform.{self.options.mail_from_domain}",
                "subject": "Leave Assistant: LLM provider degraded",
                "text": f"In the last {window_minutes} minutes:\n" + "\n".join(lines) + "\n",
            }
        return report

    async def platform_health(self) -> dict[str, Any]:
        """`GET /platform/health` (P): counts only, per tenant (doc 09 §2.9; doc 10 §7)."""
        tenants_out = []
        for tenant in await self._tenants():
            async with self._uow(system(tenant.id)) as uow:
                events = await uow.integration_events.counts()
                calendar = await uow.calendar_jobs.counts()
            tenants_out.append(
                {
                    "tenant_id": str(tenant.id),
                    "slug": tenant.slug,
                    "events_dead": events.get("dead", 0),
                    "events_undelivered": events.get("undelivered", 0),
                    "calendar_dead": calendar.get(SyncStatus.DEAD.value, 0),
                    "calendar_parked": calendar.get(SyncStatus.PARKED.value, 0),
                }
            )
        async with self._uow(None) as uow:
            jobs = await uow.jobs.counts()
        digest = await self.health_digest()
        return {
            "tenants": tenants_out,
            "jobs": jobs,
            "providers": digest.providers,
            "providers_healthy": digest.healthy,
        }

    # -- n8n error sink -----------------------------------------------------------------------------------------
    async def record_automation_error(self, principal: Principal, details: dict[str, Any]) -> None:
        self._require(principal)
        safe = {
            k: str(v)[:500]
            for k, v in details.items()
            if k in ("workflow", "node", "message", "execution_id", "mode")
        }
        async with self._uow(
            TenantContext(tenant_id=None, user_id=principal.user_id, platform_admin=True)
        ) as uow:
            await self._audit.record(
                uow,
                principal,
                "automation.workflow.failed",
                resource_type="automation",
                resource_id=safe.get("workflow"),
                details=safe,
                actor_kind=ActorKind.AUTOMATION,
            )
            await uow.commit()
