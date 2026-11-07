"""SQLAlchemy unit of work implementing `application.ports.UnitOfWork` (one session, one transaction)."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from leave_assistant.application.errors import ConflictError
from leave_assistant.application.ports import (
    AclRepository,
    AgentRunRepository,
    ApprovalRuleRepository,
    ApprovalStepRepository,
    AuditRepository,
    CalendarConnectionRepository,
    CalendarEventRepository,
    CalendarSyncJobRepository,
    ChunkRepository,
    ConversationRepository,
    DepartmentRepository,
    DocumentRepository,
    DocumentVersionRepository,
    DownloadTokenRepository,
    EmployeeRepository,
    HolidayRepository,
    IdempotencyRepository,
    IndexGenerationRepository,
    InjectionFindingRepository,
    IntegrationEventRepository,
    JobRepository,
    LeavePolicyRepository,
    LeaveRequestRepository,
    LeaveTypeRepository,
    LedgerRepository,
    MessageRepository,
    MockCalendarStore,
    NotificationRepository,
    OAuthStateRepository,
    PendingInterruptRepository,
    PlatformSettingsRepository,
    ProposalRepository,
    ProviderEventRepository,
    ReconciliationItemRepository,
    RequestEventRepository,
    RetentionPolicyRepository,
    RetentionReviewItemRepository,
    ReviewEventRepository,
    SecurityDefaultsRepository,
    SecurityPolicyRepository,
    SupportingAccessRepository,
    SupportingDocumentRepository,
    TenantRepository,
    VaultRepository,
)
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.infrastructure.db.repositories.audit import SqlAuditRepository
from leave_assistant.infrastructure.db.repositories.calendar import (
    SqlCalendarConnectionRepository,
    SqlCalendarEventRepository,
    SqlCalendarSyncJobRepository,
    SqlMockCalendarStore,
    SqlOAuthStateRepository,
    SqlReconciliationItemRepository,
)
from leave_assistant.infrastructure.db.repositories.chunks import SqlChunkRepository
from leave_assistant.infrastructure.db.repositories.conversations import (
    SqlAgentRunRepository,
    SqlConversationRepository,
    SqlMessageRepository,
    SqlProviderEventRepository,
)
from leave_assistant.infrastructure.db.repositories.departments import SqlDepartmentRepository
from leave_assistant.infrastructure.db.repositories.documents import (
    SqlAclRepository,
    SqlDocumentRepository,
    SqlDocumentVersionRepository,
    SqlIndexGenerationRepository,
    SqlReviewEventRepository,
)
from leave_assistant.infrastructure.db.repositories.employees import SqlEmployeeRepository
from leave_assistant.infrastructure.db.repositories.idempotency import SqlIdempotencyRepository
from leave_assistant.infrastructure.db.repositories.jobs import SqlJobRepository
from leave_assistant.infrastructure.db.repositories.leave_catalog import (
    SqlApprovalRuleRepository,
    SqlHolidayRepository,
    SqlLeavePolicyRepository,
    SqlLeaveTypeRepository,
)
from leave_assistant.infrastructure.db.repositories.leave_requests import (
    SqlApprovalStepRepository,
    SqlLeaveRequestRepository,
    SqlRequestEventRepository,
)
from leave_assistant.infrastructure.db.repositories.ledger import SqlLedgerRepository
from leave_assistant.infrastructure.db.repositories.notifications import (
    SqlIntegrationEventRepository,
    SqlNotificationRepository,
)
from leave_assistant.infrastructure.db.repositories.platform_settings import SqlPlatformSettingsRepository
from leave_assistant.infrastructure.db.repositories.proposals import (
    SqlPendingInterruptRepository,
    SqlProposalRepository,
)
from leave_assistant.infrastructure.db.repositories.retention import (
    SqlRetentionPolicyRepository,
    SqlRetentionReviewItemRepository,
)
from leave_assistant.infrastructure.db.repositories.security import (
    SqlInjectionFindingRepository,
    SqlSecurityDefaultsRepository,
    SqlSecurityPolicyRepository,
    SqlVaultRepository,
)
from leave_assistant.infrastructure.db.repositories.supporting import (
    SqlDownloadTokenRepository,
    SqlSupportingAccessRepository,
    SqlSupportingDocumentRepository,
)
from leave_assistant.infrastructure.db.repositories.tenants import SqlTenantRepository
from leave_assistant.infrastructure.db.session import CONTEXT_KEY


class SqlAlchemyUnitOfWork:
    tenants: TenantRepository
    departments: DepartmentRepository
    employees: EmployeeRepository
    audit: AuditRepository
    idempotency: IdempotencyRepository
    leave_types: LeaveTypeRepository
    leave_policies: LeavePolicyRepository
    holidays: HolidayRepository
    approval_rules: ApprovalRuleRepository
    leave_requests: LeaveRequestRepository
    approval_steps: ApprovalStepRepository
    request_events: RequestEventRepository
    ledger: LedgerRepository
    notifications: NotificationRepository
    integration_events: IntegrationEventRepository
    documents: DocumentRepository
    document_versions: DocumentVersionRepository
    chunks: ChunkRepository
    document_acl: AclRepository
    review_events: ReviewEventRepository
    index_generations: IndexGenerationRepository
    jobs: JobRepository
    pii_vault: VaultRepository
    platform_settings: PlatformSettingsRepository
    security_defaults: SecurityDefaultsRepository
    security_policies: SecurityPolicyRepository
    injection_findings: InjectionFindingRepository
    supporting_documents: SupportingDocumentRepository
    supporting_access: SupportingAccessRepository
    download_tokens: DownloadTokenRepository
    retention_policies: RetentionPolicyRepository
    retention_items: RetentionReviewItemRepository
    conversations: ConversationRepository
    messages: MessageRepository
    agent_runs: AgentRunRepository
    provider_events: ProviderEventRepository
    proposals: ProposalRepository
    pending_interrupts: PendingInterruptRepository
    calendar_connections: CalendarConnectionRepository
    calendar_jobs: CalendarSyncJobRepository
    calendar_events: CalendarEventRepository
    calendar_items: ReconciliationItemRepository
    mock_calendar: MockCalendarStore
    oauth_states: OAuthStateRepository

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], context: TenantContext | None
    ) -> None:
        self._factory = session_factory
        self.context = context
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("unit of work is not active")
        return self._session

    async def __aenter__(self) -> Self:
        session = self._factory()
        session.info[CONTEXT_KEY] = self.context
        self._session = session
        tenant_id = self.context.tenant_id if self.context else None
        self.tenants = SqlTenantRepository(session)
        self.departments = SqlDepartmentRepository(session, tenant_id)
        self.employees = SqlEmployeeRepository(session, tenant_id)
        self.audit = SqlAuditRepository(session, self.context)
        self.idempotency = SqlIdempotencyRepository(session)
        self.leave_types = SqlLeaveTypeRepository(session, tenant_id)
        self.leave_policies = SqlLeavePolicyRepository(session, tenant_id)
        self.holidays = SqlHolidayRepository(session, tenant_id)
        self.approval_rules = SqlApprovalRuleRepository(session, tenant_id)
        self.leave_requests = SqlLeaveRequestRepository(session, tenant_id)
        self.approval_steps = SqlApprovalStepRepository(session, tenant_id)
        self.request_events = SqlRequestEventRepository(session, tenant_id)
        self.ledger = SqlLedgerRepository(session, tenant_id)
        self.notifications = SqlNotificationRepository(session, tenant_id)
        self.integration_events = SqlIntegrationEventRepository(session, tenant_id)
        self.documents = SqlDocumentRepository(session, tenant_id)
        self.document_versions = SqlDocumentVersionRepository(session, tenant_id)
        self.chunks = SqlChunkRepository(session, tenant_id)
        self.document_acl = SqlAclRepository(session, tenant_id)
        self.review_events = SqlReviewEventRepository(session, tenant_id)
        self.index_generations = SqlIndexGenerationRepository(session, tenant_id)
        self.jobs = SqlJobRepository(session)
        self.pii_vault = SqlVaultRepository(session, tenant_id)
        self.platform_settings = SqlPlatformSettingsRepository(session)
        self.security_defaults = SqlSecurityDefaultsRepository(session)
        self.security_policies = SqlSecurityPolicyRepository(session, tenant_id)
        self.injection_findings = SqlInjectionFindingRepository(session, tenant_id)
        self.supporting_documents = SqlSupportingDocumentRepository(session, tenant_id)
        self.supporting_access = SqlSupportingAccessRepository(session, tenant_id)
        self.download_tokens = SqlDownloadTokenRepository(session)
        self.retention_policies = SqlRetentionPolicyRepository(session, tenant_id)
        self.retention_items = SqlRetentionReviewItemRepository(session, tenant_id)
        self.conversations = SqlConversationRepository(session, tenant_id)
        self.messages = SqlMessageRepository(session, tenant_id)
        self.agent_runs = SqlAgentRunRepository(session, tenant_id)
        self.provider_events = SqlProviderEventRepository(session, tenant_id)
        self.proposals = SqlProposalRepository(session, tenant_id)
        self.pending_interrupts = SqlPendingInterruptRepository(session, tenant_id)
        self.calendar_connections = SqlCalendarConnectionRepository(session, tenant_id)
        self.calendar_jobs = SqlCalendarSyncJobRepository(session, tenant_id)
        self.calendar_events = SqlCalendarEventRepository(session, tenant_id)
        self.calendar_items = SqlReconciliationItemRepository(session, tenant_id)
        self.mock_calendar = SqlMockCalendarStore(session, tenant_id)
        self.oauth_states = SqlOAuthStateRepository(session)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        try:
            await session.rollback()  # no-op after commit; discards uncommitted work otherwise
        finally:
            await session.close()

    async def commit(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("the change conflicts with existing data") from exc

    async def rollback(self) -> None:
        await self.session.rollback()


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    def __call__(self, context: TenantContext | None) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._factory, context)
