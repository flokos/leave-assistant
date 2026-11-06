"""Ports (interfaces) implemented by infrastructure adapters (doc 03 §4.3). Application code depends on these only."""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from leave_assistant.application.assistant.models import ActionProposal, PendingInterrupt
from leave_assistant.application.audit.entry import AuditEntry
from leave_assistant.application.calendar.models import (
    CalendarConnection,
    CalendarEventRecord,
    CalendarSyncJob,
    OAuthState,
    ReconciliationItem,
)
from leave_assistant.application.chat.models import (
    AgentRun,
    Conversation,
    ConversationMessage,
    ProviderEventRecord,
)
from leave_assistant.application.idempotency import IdempotencyRecord
from leave_assistant.application.jobs.models import Job, JobStatus
from leave_assistant.application.notifications.models import IntegrationEvent, Notification
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.calendar.model import ConnectionKind
from leave_assistant.domain.documents.fusion import LegHit
from leave_assistant.domain.documents.types import (
    AclEntry,
    Category,
    Chunk,
    Document,
    DocumentVersion,
    IndexGeneration,
    ParsedDocument,
    ReviewEvent,
    RiskTier,
    VersionStatus,
)
from leave_assistant.domain.documents.visibility import Reader
from leave_assistant.domain.leave.ledger import LedgerEntry
from leave_assistant.domain.leave.request import ApprovalStep, LeaveRequest, RequestEventRecord
from leave_assistant.domain.leave.state_machine import RequestStatus
from leave_assistant.domain.leave.types import ApprovalRule, LeavePolicy, LeaveType, PublicHoliday, StepRole
from leave_assistant.domain.privacy.types import EntityType, Finding
from leave_assistant.domain.retention.eligibility import DocumentClass, RetentionAction, ReviewStatus
from leave_assistant.domain.tenancy import Department, Employee, Role, Tenant


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> UUID: ...


class TenantRepository(Protocol):
    async def get(self, tenant_id: UUID) -> Tenant | None: ...
    async def get_by_slug(self, slug: str) -> Tenant | None: ...
    async def list(self, *, limit: int, after: tuple[datetime, UUID] | None) -> builtins.list[Tenant]: ...
    async def add(self, tenant: Tenant) -> None: ...
    async def update(self, tenant: Tenant) -> None: ...


@dataclass(frozen=True, slots=True)
class EmployeeFilter:
    department_id: UUID | None = None
    status: str | None = None
    query: str | None = None


class DepartmentRepository(Protocol):
    """Tenant-scoped: every method applies the unit of work's tenant context (belt) on top of RLS (braces)."""

    async def get(self, department_id: UUID) -> Department | None: ...
    async def get_by_code(self, code: str) -> Department | None: ...
    async def list(self) -> builtins.list[Department]: ...
    async def add(self, department: Department) -> None: ...
    async def update(self, department: Department) -> None: ...
    async def delete(self, department_id: UUID) -> None: ...


class EmployeeRepository(Protocol):
    async def get(self, employee_id: UUID) -> Employee | None: ...
    async def get_by_sub(self, keycloak_sub: str) -> Employee | None: ...
    async def get_by_email(self, email: str) -> Employee | None: ...
    async def get_many(self, employee_ids: Sequence[UUID]) -> dict[UUID, Employee]: ...
    async def list_active(
        self, *, department_id: UUID | None = None, role: Role | None = None
    ) -> builtins.list[Employee]: ...
    async def list(
        self, *, filters: EmployeeFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> builtins.list[Employee]: ...
    async def count_in_department(self, department_id: UUID) -> int: ...
    async def add(self, employee: Employee) -> None: ...
    async def update(self, employee: Employee) -> None: ...
    async def set_roles(self, employee_id: UUID, roles: frozenset[Role]) -> None: ...


@dataclass(frozen=True, slots=True)
class AuditFilter:
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    action: str | None = None
    platform_only: bool = False


class AuditRepository(Protocol):
    async def add(self, entry: AuditEntry) -> None: ...
    async def list(
        self, *, filters: AuditFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> builtins.list[AuditEntry]: ...


class IdempotencyRepository(Protocol):
    async def get(self, user_id: str, route: str, key: str) -> IdempotencyRecord | None: ...
    async def add(self, record: IdempotencyRecord) -> None: ...
    async def purge_expired(self, now: datetime) -> int: ...


@dataclass(frozen=True, slots=True)
class RequestFilter:
    employee_id: UUID | None = None
    department_id: UUID | None = None
    statuses: frozenset[RequestStatus] | None = None
    date_from: date | None = None  # requests ending on/after
    date_to: date | None = None  # requests starting on/before


@dataclass(frozen=True, slots=True)
class BalanceRow:
    employee_id: UUID
    leave_type_id: UUID
    period_year: int
    balance_days: Decimal
    pending_days: Decimal


class LeaveTypeRepository(Protocol):
    async def get(self, leave_type_id: UUID) -> LeaveType | None: ...
    async def get_by_code(self, code: str) -> LeaveType | None: ...
    async def list(self, *, include_inactive: bool = False) -> builtins.list[LeaveType]: ...
    async def add(self, leave_type: LeaveType) -> None: ...
    async def update(self, leave_type: LeaveType) -> None: ...


class LeavePolicyRepository(Protocol):
    async def get(self, policy_id: UUID) -> LeavePolicy | None: ...
    async def get_for(self, leave_type_id: UUID, year: int) -> LeavePolicy | None: ...
    async def list(self, *, year: int | None = None) -> builtins.list[LeavePolicy]: ...
    async def add(self, policy: LeavePolicy) -> None: ...
    async def update(self, policy: LeavePolicy) -> None: ...
    async def delete(self, policy_id: UUID) -> None: ...


class HolidayRepository(Protocol):
    async def get(self, holiday_id: UUID) -> PublicHoliday | None: ...
    async def list(self, *, year: int | None = None) -> builtins.list[PublicHoliday]: ...
    async def add(self, holiday: PublicHoliday) -> None: ...
    async def update(self, holiday: PublicHoliday) -> None: ...
    async def delete(self, holiday_id: UUID) -> None: ...


class ApprovalRuleRepository(Protocol):
    async def get(self, rule_id: UUID) -> ApprovalRule | None: ...
    async def list(self, *, include_inactive: bool = False) -> builtins.list[ApprovalRule]: ...
    async def add(self, rule: ApprovalRule) -> None: ...
    async def update(self, rule: ApprovalRule) -> None: ...
    async def delete(self, rule_id: UUID) -> None: ...


class LeaveRequestRepository(Protocol):
    async def get(self, request_id: UUID) -> LeaveRequest | None: ...
    async def list(
        self, *, filters: RequestFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> builtins.list[LeaveRequest]: ...
    async def list_for_employee(self, employee_id: UUID) -> builtins.list[LeaveRequest]: ...
    async def list_in_window(
        self, start: date, end: date, *, statuses: frozenset[RequestStatus]
    ) -> builtins.list[LeaveRequest]: ...
    async def list_by_status(self, statuses: frozenset[RequestStatus]) -> builtins.list[LeaveRequest]: ...
    async def add(self, request: LeaveRequest) -> None: ...
    async def update(self, request: LeaveRequest, *, expected_version: int, bump: bool = True) -> None: ...
    async def lock_employee(self, employee_id: UUID) -> None: ...
    async def next_request_number(self, year: int) -> str: ...
    async def get_by_number(self, request_number: str) -> LeaveRequest | None: ...
    async def find_by_idempotency_key(self, employee_id: UUID, key: str) -> LeaveRequest | None: ...


class ApprovalStepRepository(Protocol):
    async def get(self, step_id: UUID) -> ApprovalStep | None: ...
    async def list_for_request(self, request_id: UUID) -> builtins.list[ApprovalStep]: ...
    async def list_pending(
        self, *, designated_employee_id: UUID | None = None, role: StepRole | None = None
    ) -> builtins.list[ApprovalStep]: ...
    async def add(self, step: ApprovalStep) -> None: ...
    async def update(self, step: ApprovalStep, *, expected_version: int) -> None: ...


class RequestEventRepository(Protocol):
    async def add(self, event: RequestEventRecord) -> None: ...
    async def list_for_request(self, request_id: UUID) -> builtins.list[RequestEventRecord]: ...
    async def find_by_proposal(self, proposal_id: UUID) -> RequestEventRecord | None:
        """The event an agent execution wrote (payload `proposal_id`) — exactly-once recovery (M7)."""
        ...


class LedgerRepository(Protocol):
    async def add(self, entry: LedgerEntry) -> None: ...
    async def exists(self, idempotency_key: str) -> bool: ...
    async def list(
        self, *, employee_id: UUID, leave_type_id: UUID | None = None, year: int | None = None
    ) -> builtins.list[LedgerEntry]: ...
    async def balances(self, *, year: int, employee_id: UUID | None = None) -> builtins.list[BalanceRow]: ...


class NotificationRepository(Protocol):
    async def add(self, notification: Notification) -> None: ...
    async def get(self, notification_id: UUID) -> Notification | None: ...
    async def list(
        self,
        *,
        recipient_employee_id: UUID,
        unread_only: bool,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> builtins.list[Notification]: ...
    async def update(self, notification: Notification) -> None: ...
    async def mark_all_read(self, recipient_employee_id: UUID, read_at: datetime) -> int: ...
    async def exists_dedupe(self, dedupe_key: str) -> bool: ...


class IntegrationEventRepository(Protocol):
    async def add(self, event: IntegrationEvent) -> None: ...
    async def lease(
        self, *, now: datetime, until: datetime, limit: int, max_attempts: int
    ) -> builtins.list[IntegrationEvent]:
        """Undelivered, live, unleased events in id order; leases them and counts the attempt (M8 decision 8).
        Events past `max_attempts` become dead instead of being returned."""
        ...

    async def ack(self, ids: Sequence[UUID], now: datetime) -> int: ...
    async def mark_dead(self, ids: Sequence[UUID]) -> int: ...
    async def counts(self) -> dict[str, int]: ...
    async def purge_delivered(self, before: datetime) -> int: ...


# --- calendar (M8, doc 10) -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncJobFilter:
    status: str | None = None
    request_id: UUID | None = None
    employee_id: UUID | None = None  # only jobs of this employee's requests


class CalendarConnectionRepository(Protocol):
    async def add(self, connection: CalendarConnection) -> None: ...
    async def get(self, connection_id: UUID) -> CalendarConnection | None: ...
    async def find_live(self, kind: ConnectionKind, employee_id: UUID | None) -> CalendarConnection | None:
        """The non-revoked connection of this kind (company: per tenant; personal: per employee)."""
        ...

    async def list_live(self) -> builtins.list[CalendarConnection]: ...
    async def update(self, connection: CalendarConnection) -> None: ...


class CalendarSyncJobRepository(Protocol):
    async def add(self, job: CalendarSyncJob) -> None: ...
    async def get(self, job_id: UUID) -> CalendarSyncJob | None: ...
    async def get_by_key(self, idempotency_key: str) -> CalendarSyncJob | None: ...
    async def lock(self, job_id: UUID) -> CalendarSyncJob | None:
        """`SELECT … FOR UPDATE SKIP LOCKED`: `None` when another worker holds it."""
        ...

    async def list(self, *, filters: SyncJobFilter, limit: int) -> builtins.list[CalendarSyncJob]: ...
    async def list_parked(self, connection_id: UUID) -> builtins.list[CalendarSyncJob]: ...
    async def list_due(self, now: datetime, limit: int) -> builtins.list[CalendarSyncJob]:
        """Pending jobs whose next attempt is due (the sweep re-enqueues them if their queue job was lost)."""
        ...

    async def latest_for_request(self, request_id: UUID, connection_id: UUID) -> CalendarSyncJob | None: ...
    async def update(self, job: CalendarSyncJob) -> None: ...
    async def counts(self) -> dict[str, int]: ...


class CalendarEventRepository(Protocol):
    async def get(self, connection_id: UUID, request_id: UUID) -> CalendarEventRecord | None: ...
    async def add(self, record: CalendarEventRecord) -> None: ...
    async def update(self, record: CalendarEventRecord) -> None: ...
    async def list_for_connection(self, connection_id: UUID) -> builtins.list[CalendarEventRecord]: ...
    async def mark_orphaned(self, connection_id: UUID, now: datetime) -> int: ...


class ReconciliationItemRepository(Protocol):
    async def add(self, item: ReconciliationItem) -> None: ...
    async def get(self, item_id: UUID) -> ReconciliationItem | None: ...
    async def update(self, item: ReconciliationItem) -> None: ...
    async def latest_run(self) -> builtins.list[ReconciliationItem]:
        """The items of the most recent run (any connection of the tenant)."""
        ...


class MockCalendarStore(Protocol):
    """The mock provider's events (`app.mock_calendar_events`), keyed by calendar and event id."""

    async def get(self, calendar_id: str, event_id: str) -> dict[str, Any] | None: ...
    async def put(self, calendar_id: str, event_id: str, body: dict[str, Any], now: datetime) -> bool:
        """Insert; `False` when the id exists (the provider's 409)."""
        ...

    async def replace(self, calendar_id: str, event_id: str, body: dict[str, Any], now: datetime) -> bool: ...
    async def remove(self, calendar_id: str, event_id: str) -> bool: ...
    async def list(self, calendar_id: str) -> builtins.list[dict[str, Any]]: ...


class OAuthStateRepository(Protocol):
    """`platform.oauth_states` (no tenant context at the callback)."""

    async def add(self, state: OAuthState) -> None: ...
    async def consume(self, nonce_hash: str, now: datetime) -> OAuthState | None:
        """Atomically mark used; `None` when unknown, used or expired."""
        ...

    async def purge(self, before: datetime) -> int: ...


# --- documents, retrieval and jobs (M4, doc 05 / doc 07 §4.4 / ADR-0016 / ADR-0019) --------------------------


class DocumentStorage(Protocol):
    """Object storage behind ADR-0016 keys. Browsers never see storage URLs; downloads stream through the API."""

    async def put_file(self, key: str, path: Path, *, content_type: str) -> None: ...
    async def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None: ...
    async def get_bytes(self, key: str) -> bytes: ...
    async def download_to(self, key: str, path: Path) -> None: ...
    def stream(self, key: str) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...
    async def exists(self, key: str) -> bool: ...


class DocumentParser(Protocol):
    """Blocking (Docling + Tesseract); the pipeline calls it in a thread of the worker's child process."""

    def parse(self, path: Path, mime: str, *, max_pages: int | None = None) -> ParsedDocument: ...


class Tokenizer(Protocol):
    def count(self, text: str) -> int: ...


class EmbeddingModel(Protocol):
    """Prefix discipline (`passage:` / `query:`) is enforced by the adapter; callers pass raw text (ADR-0013)."""

    @property
    def model_id(self) -> str: ...
    @property
    def dimension(self) -> int: ...
    def embed_passages(self, texts: Sequence[str]) -> builtins.list[builtins.list[float]]: ...
    def embed_query(self, text: str) -> builtins.list[float]: ...


class LanguageDetector(Protocol):
    def detect(self, text: str) -> str | None: ...  # "el" | "en" | None


class Reranker(Protocol):
    """Cross-encoder relevance scores in [0, 1] for (query, passage) pairs (ADR-0014; blocking — call in a thread)."""

    @property
    def model_id(self) -> str: ...
    def score(self, query: str, passages: Sequence[str]) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    score: float = 0.0
    tier: RiskTier = RiskTier.LOW
    findings: tuple[dict[str, Any], ...] = ()
    chunk_flags: dict[int, tuple[str, ...]] = field(default_factory=dict)  # by chunk ordinal
    heuristic_score: float = 0.0
    classifier_score: float | None = None
    structural_flags: tuple[str, ...] = ()


class SimilarityClassifier(Protocol):
    """Calibrated 0–1 injection likelihood per text from e5 similarity to curated exemplars (doc 08 §8.2)."""

    @property
    def version(self) -> str: ...
    def score_blocks(self, texts: Sequence[str]) -> builtins.list[float]: ...


class LlmJudge(Protocol):
    """Optional judge for the medium band (adapter arrives with the provider router in M6)."""

    async def judge(self, excerpts: Sequence[str]) -> float | None: ...


# --- privacy (doc 08 §7, ADR-0017) ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KnownEntity:
    """A tenant employee as the known-entity recogniser sees it (the strongest detection layer)."""

    employee_id: UUID
    first_name: str
    last_name: str
    email: str
    department_id: UUID | None = None


class PiiEngine(Protocol):
    """Blocking detection (Presidio + Greek recognisers + known entities); runs in the worker child."""

    def detect(
        self, text: str, *, language: str | None, known: Sequence[KnownEntity]
    ) -> builtins.list[Finding]: ...


class VaultCipher(Protocol):
    """HMAC lookups and AES-256-GCM originals with keys from the environment; `configured` = keys present."""

    @property
    def configured(self) -> bool: ...
    def hmac(self, tenant_id: UUID, key: str) -> bytes: ...
    def encrypt(self, tenant_id: UUID, value: str) -> bytes: ...
    def decrypt(self, tenant_id: UUID, data: bytes) -> str: ...


@dataclass(slots=True)
class VaultEntry:
    id: UUID
    tenant_id: UUID
    entity_type: EntityType
    value_hmac: bytes
    value_encrypted: bytes
    placeholder: str
    first_seen_at: datetime
    last_seen_at: datetime
    source_kinds: tuple[str, ...] = ()
    subject_employee_id: UUID | None = None


class VaultRepository(Protocol):
    async def get_by_hmac(self, value_hmac: bytes) -> VaultEntry | None: ...
    async def get_by_placeholder(self, placeholder: str) -> VaultEntry | None: ...
    async def next_sequence(self, entity_type: EntityType) -> int: ...
    async def add(self, entry: VaultEntry) -> None: ...
    async def update(self, entry: VaultEntry) -> None: ...
    async def count(self) -> int: ...


# --- security policies and findings (doc 07 §4.5) --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SecurityDefaults:
    """`platform.security_policy_defaults`: tenant defaults plus the ranges tenants may configure within."""

    risk_low_max: float = 0.3
    risk_medium_max: float = 0.7
    injection_llm_judge: bool = False
    user_input_block_threshold: float = 0.85
    max_upload_mb: int = 25
    allowed_mime: tuple[str, ...] = (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
    )
    threshold_min: float = 0.05
    threshold_max: float = 0.95
    max_upload_mb_limit: int = 50
    updated_by: str = "system"


@dataclass(slots=True)
class SecurityPolicy:
    tenant_id: UUID
    risk_low_max: float
    risk_medium_max: float
    injection_llm_judge: bool
    user_input_block_threshold: float
    max_upload_mb: int
    allowed_mime: tuple[str, ...]
    updated_by: str
    created_at: datetime
    updated_at: datetime


class SecurityDefaultsRepository(Protocol):
    async def get(self) -> SecurityDefaults: ...
    async def update(self, defaults: SecurityDefaults) -> None: ...


class SecurityPolicyRepository(Protocol):
    async def get(self) -> SecurityPolicy | None: ...
    async def upsert(self, policy: SecurityPolicy) -> None: ...


@dataclass(frozen=True, slots=True)
class InjectionFinding:
    id: UUID
    tenant_id: UUID
    subject_type: str  # document_version | user_message
    subject_id: str
    detector: str  # heuristic | classifier | llm_judge | structural
    score: float
    label: str
    excerpt_hash: str
    details: dict[str, Any]
    created_at: datetime


class InjectionFindingRepository(Protocol):
    async def add_many(self, findings: Sequence[InjectionFinding]) -> None: ...
    async def list_for_subject(
        self, subject_type: str, subject_id: str
    ) -> builtins.list[InjectionFinding]: ...


# --- supporting documents (doc 08 §9, ADR-0016) -------------------------------------------------------------


class FileCipher(Protocol):
    """Envelope encryption: a fresh DEK per file (AES-256-GCM), wrapped by `APP_KEK` (AES key wrap)."""

    @property
    def configured(self) -> bool: ...
    def encrypt(self, data: bytes, *, aad: bytes) -> tuple[bytes, bytes]: ...  # (ciphertext, wrapped_dek)
    def decrypt(self, ciphertext: bytes, wrapped_dek: bytes, *, aad: bytes) -> bytes: ...


@dataclass(slots=True)
class SupportingDocument:
    id: UUID
    tenant_id: UUID
    request_id: UUID
    employee_id: UUID
    object_key: str
    original_filename: str
    mime: str
    size_bytes: int
    sha256: str
    encrypted_dek: bytes
    status: str  # stored | retention_pending | deleted
    uploaded_by_employee_id: UUID
    created_at: datetime
    retention_until: date | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SupportingAccessEvent:
    id: UUID
    tenant_id: UUID
    supporting_document_id: UUID
    actor_employee_id: UUID
    action: str  # view | download | delete | download_token
    occurred_at: datetime
    reason: str | None = None
    request_id: str | None = None  # HTTP correlation id


@dataclass(slots=True)
class DownloadToken:
    token_hash: str
    tenant_id: UUID
    supporting_document_id: UUID
    actor_employee_id: UUID
    actor_user_id: str
    actor_roles: tuple[str, ...]
    expires_at: datetime
    created_at: datetime
    reason: str | None = None
    used_at: datetime | None = None


class SupportingDocumentRepository(Protocol):
    async def get(self, document_id: UUID) -> SupportingDocument | None: ...
    async def list_for_request(self, request_id: UUID) -> builtins.list[SupportingDocument]: ...
    async def count_for_requests(self, request_ids: Sequence[UUID]) -> dict[UUID, int]: ...
    async def list_by_status(self, status: str) -> builtins.list[SupportingDocument]: ...
    async def add(self, document: SupportingDocument) -> None: ...
    async def update(self, document: SupportingDocument) -> None: ...


class SupportingAccessRepository(Protocol):
    async def add(self, event: SupportingAccessEvent) -> None: ...
    async def list_for_document(self, document_id: UUID) -> builtins.list[SupportingAccessEvent]: ...


class DownloadTokenRepository(Protocol):
    """`platform.supporting_download_tokens` (no tenant context when the token is presented)."""

    async def add(self, token: DownloadToken) -> None: ...
    async def consume(self, token_hash: str, now: datetime) -> DownloadToken | None: ...


# --- retention (brief §5.7, Q-08) ----------------------------------------------------------------------------


@dataclass(slots=True)
class RetentionPolicy:
    id: UUID
    tenant_id: UUID
    document_class: DocumentClass
    retention_days: int
    action: RetentionAction
    enabled: bool
    updated_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RetentionReviewItem:
    id: UUID
    tenant_id: UUID
    policy_id: UUID
    subject_type: str  # supporting_document | document_version
    subject_id: UUID
    label: str
    eligible_since: date
    status: ReviewStatus
    created_at: datetime
    reviewer_employee_id: UUID | None = None
    decided_at: datetime | None = None
    comment: str | None = None


class RetentionPolicyRepository(Protocol):
    async def list(self) -> builtins.list[RetentionPolicy]: ...
    async def get(self, policy_id: UUID) -> RetentionPolicy | None: ...
    async def get_by_class(self, document_class: DocumentClass) -> RetentionPolicy | None: ...
    async def add(self, policy: RetentionPolicy) -> None: ...
    async def update(self, policy: RetentionPolicy) -> None: ...
    async def delete(self, policy_id: UUID) -> None: ...


class RetentionReviewItemRepository(Protocol):
    async def list(self, *, status: ReviewStatus | None) -> builtins.list[RetentionReviewItem]: ...
    async def get(self, item_id: UUID) -> RetentionReviewItem | None: ...
    async def latest_for(self, subject_type: str, subject_id: UUID) -> RetentionReviewItem | None: ...
    async def add(self, item: RetentionReviewItem) -> None: ...
    async def update(self, item: RetentionReviewItem) -> None: ...


@dataclass(frozen=True, slots=True)
class DocumentFilter:
    category: Category | None = None
    status: VersionStatus | None = None  # of the active or, failing that, the latest version
    query: str | None = None  # title contains (case-insensitive)


class DocumentRepository(Protocol):
    async def get(self, document_id: UUID) -> Document | None: ...
    async def get_by_title(self, title: str) -> Document | None: ...
    async def list(
        self, *, reader: Reader, filters: DocumentFilter, limit: int, after: tuple[datetime, UUID] | None
    ) -> builtins.list[Document]: ...
    async def add(self, document: Document) -> None: ...
    async def update(self, document: Document, *, expected_version: int) -> None: ...


class DocumentVersionRepository(Protocol):
    async def get(self, version_id: UUID) -> DocumentVersion | None: ...
    async def get_by_number(self, document_id: UUID, version_no: int) -> DocumentVersion | None: ...
    async def list_for_document(self, document_id: UUID) -> builtins.list[DocumentVersion]: ...
    async def list_for_documents(
        self, document_ids: Sequence[UUID]
    ) -> dict[UUID, builtins.list[DocumentVersion]]: ...
    async def find_by_sha256(self, document_id: UUID, sha256: str) -> DocumentVersion | None: ...
    async def list_by_status(self, statuses: frozenset[VersionStatus]) -> builtins.list[DocumentVersion]: ...
    async def add(self, version: DocumentVersion) -> None: ...
    async def update(self, version: DocumentVersion) -> None: ...


class ChunkRepository(Protocol):
    """Chunks, vectors and both retrieval legs; the visibility predicate is applied inside the adapter."""

    async def replace_for_version(self, version_id: UUID, chunks: Sequence[Chunk]) -> None: ...
    async def get(self, chunk_id: UUID) -> Chunk | None: ...
    async def list_for_version(self, version_id: UUID) -> builtins.list[Chunk]: ...
    async def count_for_version(self, version_id: UUID) -> int: ...
    async def set_retrievable(self, version_id: UUID, retrievable: bool) -> int: ...
    async def vector_search(
        self, embedding: Sequence[float], *, reader: Reader, limit: int
    ) -> builtins.list[LegHit]: ...
    async def keyword_search(
        self, query: str, *, language: str | None, reader: Reader, limit: int
    ) -> builtins.list[LegHit]: ...
    async def visible_version_ids(self, *, reader: Reader) -> builtins.list[UUID]:
        """Versions whose chunks the reader may retrieve — the same predicate as both legs (M9: Chroma filter)."""
        ...

    async def index_rows(self) -> builtins.list[IndexRow]:
        """Every retrievable chunk of the tenant with its embedding (M9: copying the vectors into Chroma)."""
        ...


@dataclass(frozen=True, slots=True)
class IndexRow:
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    embedding: builtins.list[float]


class VectorIndex(Protocol):
    """The vector leg of retrieval (ADR-0011): pgvector by default; Chroma for the M9 comparison only."""

    name: str

    async def search(
        self, uow: UnitOfWork, embedding: Sequence[float], *, reader: Reader, limit: int
    ) -> builtins.list[LegHit]: ...


class AclRepository(Protocol):
    async def list_for_document(self, document_id: UUID) -> builtins.list[AclEntry]: ...
    async def replace_for_document(self, document_id: UUID, entries: Sequence[AclEntry]) -> None: ...


class ReviewEventRepository(Protocol):
    async def add(self, event: ReviewEvent) -> None: ...
    async def list_for_version(self, version_id: UUID) -> builtins.list[ReviewEvent]: ...


class IndexGenerationRepository(Protocol):
    async def get(self, generation: str) -> IndexGeneration | None: ...
    async def upsert(self, generation: IndexGeneration) -> None: ...
    async def list(self) -> builtins.list[IndexGeneration]: ...


@dataclass(frozen=True, slots=True)
class JobFilter:
    status: JobStatus | None = None
    type: str | None = None
    tenant_id: UUID | None = None


class JobRepository(Protocol):
    """`platform.jobs`: not tenant-scoped (the worker claims across tenants); payloads carry identifiers only."""

    async def add(self, job: Job) -> None: ...
    async def get(self, job_id: UUID) -> Job | None: ...
    async def find_active_by_dedupe(self, dedupe_key: str) -> Job | None: ...
    async def claim(
        self, *, now: datetime, worker_id: str, stale_before: datetime, types: Sequence[str] | None
    ) -> Job | None: ...
    async def update(self, job: Job) -> None: ...
    async def list(self, *, filters: JobFilter, limit: int) -> builtins.list[Job]: ...
    async def counts(self) -> dict[str, int]: ...
    async def purge_finished(self, before: datetime) -> int:
        """Delete succeeded jobs finished before `before` (M8 housekeeping; failed jobs stay for operators)."""
        ...


class UnitOfWork(Protocol):
    """One database transaction. Entering opens a session bound to `context` (SET LOCAL app.* — ADR-0003)."""

    context: TenantContext | None
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

    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self, context: TenantContext | None) -> UnitOfWork: ...


@dataclass(frozen=True, slots=True)
class IdentityUser:
    sub: str
    username: str
    enabled: bool
    required_actions: tuple[str, ...] = ()
    realm_roles: frozenset[str] = frozenset()
    group_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NewIdentityUser:
    username: str
    email: str
    first_name: str
    last_name: str
    locale: str
    password: str
    temporary_password: bool
    realm_roles: frozenset[str]
    group_path: str
    attributes: dict[str, Sequence[str]] = field(default_factory=dict)


class IdentityAdmin(Protocol):
    """Keycloak Admin API operations used by provisioning (ADR-0004). Implemented by Keycloak and a fake."""

    async def ensure_tenant_group(self, slug: str, tenant_id: UUID) -> str: ...
    async def find_user_by_username(self, username: str) -> IdentityUser | None: ...
    async def get_user(self, sub: str) -> IdentityUser | None: ...
    async def create_user(self, spec: NewIdentityUser) -> str: ...
    async def update_user(self, sub: str, *, first_name: str, last_name: str, locale: str) -> None: ...
    async def set_enabled(self, sub: str, enabled: bool) -> None: ...
    async def set_realm_roles(self, sub: str, roles: frozenset[str], *, managed: frozenset[str]) -> None: ...
    async def reset_password(self, sub: str, password: str, *, temporary: bool) -> None: ...
    async def delete_user(self, sub: str) -> None: ...


# --- conversations (M6, doc 07 §4.6) ---------------------------------------------------------------------------


class ConversationRepository(Protocol):
    async def get(self, conversation_id: UUID) -> Conversation | None: ...
    async def list_for_employee(
        self, employee_id: UUID, *, limit: int, after: tuple[datetime, UUID] | None
    ) -> builtins.list[Conversation]: ...
    async def add(self, conversation: Conversation) -> None: ...
    async def update(self, conversation: Conversation) -> None: ...
    async def try_lock(self, conversation_id: UUID, *, until: datetime, now: datetime) -> bool: ...
    async def unlock(self, conversation_id: UUID) -> None: ...
    async def expired(self, *, inactive_before: datetime) -> builtins.list[Conversation]: ...
    async def delete(self, conversation_id: UUID) -> None: ...


class MessageRepository(Protocol):
    async def add(self, message: ConversationMessage) -> None: ...
    async def list_for_conversation(self, conversation_id: UUID) -> builtins.list[ConversationMessage]: ...


class AgentRunRepository(Protocol):
    async def add(self, run: AgentRun) -> None: ...
    async def count_for_conversation(self, conversation_id: UUID) -> int: ...
    async def list_for_conversation(self, conversation_id: UUID) -> builtins.list[AgentRun]: ...


class ProviderEventRepository(Protocol):
    async def add_many(self, events: Sequence[ProviderEventRecord]) -> None: ...
    async def counts_since(self, since: datetime) -> dict[tuple[str, str], int]:
        """(provider, event) → occurrences since `since` (M8: provider health digest)."""
        ...


# --- proposals (M7, doc 07 §4.3/§4.6) --------------------------------------------------------------------------


class ProposalRepository(Protocol):
    async def add(self, proposal: ActionProposal) -> None: ...
    async def get(self, proposal_id: UUID) -> ActionProposal | None: ...
    async def update(self, proposal: ActionProposal) -> None: ...
    async def claim(self, proposal_id: UUID, *, run_id: UUID, now: datetime) -> ActionProposal | None:
        """Atomic `pending → confirmed` (only while not expired); None when someone else got there first."""
        ...

    async def list_for_conversation(self, conversation_id: UUID) -> builtins.list[ActionProposal]: ...
    async def due_for_expiry(self, now: datetime) -> builtins.list[ActionProposal]: ...


class PendingInterruptRepository(Protocol):
    async def put(self, interrupt: PendingInterrupt) -> None: ...
    async def get(self, conversation_id: UUID) -> PendingInterrupt | None: ...
    async def delete(self, conversation_id: UUID) -> None: ...
