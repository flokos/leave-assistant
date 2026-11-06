"""The application services wired by the composition root (main.py / worker) and consumed by the API layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.chat.service import ChatService
from leave_assistant.application.documents.ingestion import IngestionPipeline
from leave_assistant.application.documents.service import DocumentLimits, DocumentService
from leave_assistant.application.identity.claims import TokenClaims
from leave_assistant.application.jobs.service import JobService
from leave_assistant.application.leave.approvals import ApprovalService
from leave_assistant.application.leave.catalog import LeaveCatalogService
from leave_assistant.application.leave.ledger import LedgerService
from leave_assistant.application.leave.planner import RoutingPlanner
from leave_assistant.application.leave.requests import LeaveRequestService, RequestWorkflow
from leave_assistant.application.leave.rollover import RolloverService
from leave_assistant.application.leave.team import TeamService
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.ports import (
    Clock,
    DocumentParser,
    DocumentStorage,
    EmbeddingModel,
    FileCipher,
    IdentityAdmin,
    IdGenerator,
    LanguageDetector,
    LlmJudge,
    PiiEngine,
    Reranker,
    SimilarityClassifier,
    Tokenizer,
    UnitOfWorkFactory,
    VaultCipher,
)
from leave_assistant.application.privacy.reidentification import ReidentificationService
from leave_assistant.application.privacy.service import PseudonymisationService
from leave_assistant.application.retention.service import RetentionService
from leave_assistant.application.retrieval.service import RetrievalService
from leave_assistant.application.security.policy import SecurityPolicyService
from leave_assistant.application.security.scan import InjectionScanService
from leave_assistant.application.supporting.service import SupportingDocumentService
from leave_assistant.application.tenancy.departments import DepartmentService
from leave_assistant.application.tenancy.employees import EmployeeService
from leave_assistant.application.tenancy.provisioning import TenantProvisioningService
from leave_assistant.domain.documents.chunking import ChunkingRules


class TokenVerifierPort(Protocol):
    async def verify(self, token: str) -> TokenClaims: ...


@dataclass(frozen=True, slots=True)
class DocumentAdapters:
    """Storage, parsing, ML, PII and crypto adapters (M4/M5) — real ones in the runtime, fakes in tests."""

    storage: DocumentStorage
    parser: DocumentParser
    tokenizer: Tokenizer
    embedder: EmbeddingModel
    language: LanguageDetector
    pii_engine: PiiEngine
    vault_cipher: VaultCipher
    file_cipher: FileCipher
    classifier: SimilarityClassifier | None = None
    judge: LlmJudge | None = None
    reranker: Reranker | None = None


@dataclass(frozen=True, slots=True)
class DocumentOptions:
    max_upload_mb: int = 25
    max_pages: int = 500
    chunking_rules_version: str = "v1"
    job_max_attempts: int = 3
    job_stale_after_seconds: float = 1800.0
    retrieval_leg_size: int = 30
    retrieval_k: int = 10
    pii_score_threshold: float = 0.4
    supporting_max_upload_mb: int = 10
    download_token_ttl_seconds: int = 60
    rerank_candidates: int = 20
    conversation_retention_days: int = 90


@dataclass(slots=True)
class Services:
    uow_factory: UnitOfWorkFactory
    identity: IdentityAdmin
    verifier: TokenVerifierPort
    clock: Clock
    ids: IdGenerator
    adapters: DocumentAdapters
    options: DocumentOptions
    audit: AuditService
    provisioning: TenantProvisioningService
    employees: EmployeeService
    departments: DepartmentService
    catalog: LeaveCatalogService
    ledger: LedgerService
    leave_requests: LeaveRequestService
    approvals: ApprovalService
    team: TeamService
    notifications: NotificationService
    rollover: RolloverService
    jobs: JobService
    documents: DocumentService
    ingestion: IngestionPipeline
    retrieval: RetrievalService
    privacy: PseudonymisationService
    reidentification: ReidentificationService
    security: InjectionScanService
    security_policies: SecurityPolicyService
    supporting: SupportingDocumentService
    retention: RetentionService
    chat: ChatService

    @classmethod
    def build(
        cls,
        *,
        uow_factory: UnitOfWorkFactory,
        identity: IdentityAdmin,
        verifier: TokenVerifierPort,
        clock: Clock,
        ids: IdGenerator,
        adapters: DocumentAdapters,
        options: DocumentOptions | None = None,
    ) -> Services:
        options = options or DocumentOptions()
        audit = AuditService(clock, ids)
        planner = RoutingPlanner(clock)
        notifications = NotificationService(uow_factory, ids, clock)
        ledger = LedgerService(uow_factory, ids, clock, audit)
        workflow = RequestWorkflow(ids, clock, audit, notifications, ledger, planner)
        leave_requests = LeaveRequestService(uow_factory, workflow)
        jobs = JobService(
            uow_factory,
            ids,
            clock,
            max_attempts=options.job_max_attempts,
            stale_after_seconds=options.job_stale_after_seconds,
        )
        rules = ChunkingRules(version=options.chunking_rules_version)
        security_policies = SecurityPolicyService(uow_factory, clock, audit)
        privacy = PseudonymisationService(
            adapters.pii_engine, adapters.vault_cipher, ids, clock, threshold=options.pii_score_threshold
        )
        security = InjectionScanService(adapters.classifier, adapters.judge)
        reidentification = ReidentificationService(privacy)
        return cls(
            uow_factory=uow_factory,
            identity=identity,
            verifier=verifier,
            clock=clock,
            ids=ids,
            adapters=adapters,
            options=options,
            audit=audit,
            provisioning=TenantProvisioningService(
                uow_factory, identity, ids, clock, audit, security_policies=security_policies
            ),
            employees=EmployeeService(uow_factory, identity, ids, clock, audit),
            departments=DepartmentService(uow_factory, ids, clock, audit),
            catalog=LeaveCatalogService(uow_factory, ids, clock, audit, planner),
            ledger=ledger,
            leave_requests=leave_requests,
            approvals=ApprovalService(uow_factory, workflow, leave_requests),
            team=TeamService(uow_factory),
            notifications=notifications,
            rollover=RolloverService(uow_factory, ids, clock, audit, ledger),
            jobs=jobs,
            documents=DocumentService(
                uow_factory,
                adapters.storage,
                ids,
                clock,
                audit,
                jobs,
                limits=DocumentLimits(max_upload_mb=options.max_upload_mb, max_pages=options.max_pages),
            ),
            ingestion=IngestionPipeline(
                uow_factory,
                adapters.storage,
                adapters.parser,
                adapters.tokenizer,
                adapters.embedder,
                adapters.language,
                security,
                privacy,
                security_policies,
                ids,
                clock,
                audit,
                notifications,
                rules=rules,
                max_pages=options.max_pages,
            ),
            retrieval=RetrievalService(
                uow_factory,
                adapters.embedder,
                adapters.language,
                leg_size=options.retrieval_leg_size,
                default_k=options.retrieval_k,
                reranker=adapters.reranker,
                rerank_candidates=options.rerank_candidates,
            ),
            privacy=privacy,
            reidentification=reidentification,
            security=security,
            security_policies=security_policies,
            supporting=SupportingDocumentService(
                uow_factory,
                adapters.storage,
                adapters.file_cipher,
                ids,
                clock,
                audit,
                notifications,
                max_upload_mb=options.supporting_max_upload_mb,
                token_ttl_seconds=options.download_token_ttl_seconds,
            ),
            retention=RetentionService(uow_factory, ids, clock, audit, notifications, jobs, adapters.storage),
            chat=ChatService(
                uow_factory,
                ids,
                clock,
                audit,
                privacy,
                reidentification,
                security_policies,
                adapters.language,
                None,  # the LangGraph agent is attached by the runtime (it needs an async checkpointer pool)
                retention_days=options.conversation_retention_days,
            ),
        )

    def with_clock(self, clock: Clock) -> Services:
        """The same adapters with another clock (the seed submits sample data 'in the past')."""
        services = Services.build(
            uow_factory=self.uow_factory,
            identity=self.identity,
            verifier=self.verifier,
            clock=clock,
            ids=self.ids,
            adapters=self.adapters,
            options=self.options,
        )
        services.chat.agent = self.chat.agent
        return services
