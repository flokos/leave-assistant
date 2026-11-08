"""IngestionPipeline (doc 05 §3): parse → normalise → language → scan → chunk → embed → index → activate.

Runs in the worker (child process). Indexing is transactional per version: chunks, vectors, vault entries and the
status flip are written in one transaction; a failure leaves no partial index. Retries are the job service's
business; this class only reports the final failure on the version.

M5: injection scoring (heuristics + similarity classifier + structural flags, thresholds from the tenant's security
policy) and pseudonymisation (PII engine → vault placeholders → `content_llm_safe`) replace the M4 no-op scanners.
A PII-engine failure or missing vault keys fail closed: the version never becomes ACTIVE (doc 08 §7.5).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from leave_assistant.application.audit.service import AuditService
from leave_assistant.application.documents.activation import activate_version
from leave_assistant.application.documents.keys import extracted_key
from leave_assistant.application.errors import ApplicationError, NotFoundError
from leave_assistant.application.notifications.models import IntegrationEvent
from leave_assistant.application.notifications.service import NotificationService
from leave_assistant.application.ports import (
    Clock,
    DocumentParser,
    DocumentStorage,
    EmbeddingModel,
    IdGenerator,
    InjectionFinding,
    KnownEntity,
    LanguageDetector,
    RiskAssessment,
    Tokenizer,
    UnitOfWork,
    UnitOfWorkFactory,
)
from leave_assistant.application.privacy.filters import organisational_terms
from leave_assistant.application.privacy.service import PseudonymisationService
from leave_assistant.application.security.policy import SecurityPolicyService
from leave_assistant.application.security.scan import InjectionScanService, excerpt_hash
from leave_assistant.application.tenancy.context import TenantContext
from leave_assistant.domain.documents.chunking import ChunkDraft, ChunkingRules, chunk_document, chunk_id_for
from leave_assistant.domain.documents.formats import DOCX, MIME_EXTENSION, PDF
from leave_assistant.domain.documents.lifecycle import VersionEvent, can, transition
from leave_assistant.domain.documents.types import (
    Block,
    BlockType,
    Chunk,
    Document,
    DocumentVersion,
    IndexGeneration,
    ParsedDocument,
    ReviewAction,
    RiskTier,
    VersionStatus,
)
from leave_assistant.domain.documents.visibility import REVIEWERS
from leave_assistant.domain.privacy.types import Finding
from leave_assistant.domain.security.structural import docx_has_macros, pdf_structural_flags

WORKER_CONTEXT_USER = "worker"
PII_ENGINE_LABEL = "presidio+known_entities"


class IngestionError(ApplicationError):
    """A processing failure with a stable reason (`no_text`, `too_many_pages`, `parse_error`, …)."""

    problem_type = "document/processing-failed"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason, reason=reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    version_id: UUID
    status: VersionStatus
    chunks: int
    pages: int | None
    language: str | None
    skipped: bool = False


def worker_context(tenant_id: UUID) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, user_id=WORKER_CONTEXT_USER, roles=("system",))


def blocks_to_json(parsed: ParsedDocument) -> dict[str, Any]:
    return {
        "pages": parsed.pages,
        "warnings": list(parsed.warnings),
        "blocks": [
            {
                "type": b.type.value,
                "text": b.text,
                "level": b.level,
                "page": b.page,
                "rows": [list(r) for r in b.rows],
            }
            for b in parsed.blocks
        ],
    }


def blocks_from_json(data: dict[str, Any]) -> ParsedDocument:
    return ParsedDocument(
        blocks=tuple(
            Block(
                type=BlockType(b["type"]),
                text=b["text"],
                level=int(b.get("level") or 0),
                page=b.get("page"),
                rows=tuple(tuple(str(c) for c in row) for row in b.get("rows") or ()),
            )
            for b in data.get("blocks", [])
        ),
        pages=data.get("pages"),
        warnings=tuple(data.get("warnings") or ()),
    )


class IngestionPipeline:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        storage: DocumentStorage,
        parser: DocumentParser,
        tokenizer: Tokenizer,
        embedder: EmbeddingModel,
        language: LanguageDetector,
        security: InjectionScanService,
        privacy: PseudonymisationService,
        policies: SecurityPolicyService,
        ids: IdGenerator,
        clock: Clock,
        audit: AuditService,
        notifications: NotificationService,
        *,
        rules: ChunkingRules | None = None,
        max_pages: int = 500,
    ) -> None:
        self._uow = uow_factory
        self._storage = storage
        self._parser = parser
        self._tokenizer = tokenizer
        self._embedder = embedder
        self._language = language
        self._security = security
        self._privacy = privacy
        self._policies = policies
        self._ids = ids
        self._clock = clock
        self._audit = audit
        self._notifications = notifications
        self._rules = rules or ChunkingRules()
        self._max_pages = max_pages

    @property
    def generation(self) -> str:
        return f"{self._embedder.model_id}|{self._rules.version}"

    # --- document.process ------------------------------------------------------------------------------------
    async def process(self, tenant_id: UUID, version_id: UUID) -> ProcessOutcome:
        context = worker_context(tenant_id)
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            if version.status in (
                VersionStatus.ACTIVE,
                VersionStatus.PENDING_REVIEW,
                VersionStatus.QUARANTINED,
            ):
                return ProcessOutcome(
                    version.id, version.status, 0, version.pages, version.language, skipped=True
                )
            if can(version.status, VersionEvent.PROCESS):
                version.status = transition(version.status, VersionEvent.PROCESS)
            elif version.status is not VersionStatus.PROCESSING:
                raise IngestionError("invalid_state", f"version is {version.status.value}")
            version.attempts += 1
            version.updated_at = self._clock.now()
            await uow.document_versions.update(version)
            policy = await self._policies.policy_for(uow, tenant_id)
            known = await self._known_entities(uow)
            terms = await self._document_terms(uow)
            await uow.commit()

        parsed, structural = await self._parse(version)
        text = parsed.text
        if not text.strip():
            raise IngestionError("no_text", "no text could be extracted (image-only document?)")
        if parsed.pages is not None and parsed.pages > self._max_pages:
            raise IngestionError(
                "too_many_pages", f"{parsed.pages} pages exceed the limit of {self._max_pages}"
            )
        language = self._language.detect(text[:20000]) or document.language_hint
        drafts = chunk_document(
            parsed.blocks, title=document.title, count_tokens=self._tokenizer.count, rules=self._rules
        )
        if not drafts:
            raise IngestionError("no_text", "the document produced no chunks")
        contents = [d.content for d in drafts]
        risk = await asyncio.to_thread(
            self._security.assess_blocks,
            contents,
            thresholds=self._policies.thresholds(policy),
            structural_flags=structural,
        )
        pii_findings = await self._detect_pii(contents, language, known, terms)
        vectors = await asyncio.to_thread(self._embedder.embed_passages, [d.embed_text for d in drafts])
        key = extracted_key(tenant_id, document.id, version.version_no)
        await self._storage.put_bytes(
            key,
            json.dumps(blocks_to_json(parsed), ensure_ascii=False).encode(),
            content_type="application/json",
        )

        now = self._clock.now()
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            chunks, pii_summary = await self._pseudonymised_chunks(
                uow, version, drafts, vectors, pii_findings, risk.chunk_flags, language
            )
            await uow.chunks.replace_for_version(version.id, chunks)
            await self._record_findings(uow, version, risk)
            version.pages = parsed.pages
            version.language = language
            version.extracted_object_key = key
            version.risk_score = risk.score
            version.risk_tier = risk.tier
            version.risk_findings = self._reviewer_findings(risk, chunks)
            version.pii_summary = pii_summary
            version.processing_error = None
            version.processed_at = now
            version.index_generation = self.generation
            version.updated_at = now
            event = {
                RiskTier.LOW: VersionEvent.ACTIVATE,
                RiskTier.MEDIUM: VersionEvent.SEND_TO_REVIEW,
                RiskTier.HIGH: VersionEvent.QUARANTINE,
            }[risk.tier]
            if event is VersionEvent.ACTIVATE:
                await activate_version(
                    uow, document, version, now=now, audit=self._audit, principal=None, event=event
                )
            else:
                version.status = transition(version.status, event)
                version.retrievable = False
                await uow.document_versions.update(version)
                await self._notify_reviewers(uow, document, version)
            await self._ensure_generation(uow, tenant_id, now)
            await self._audit.record(
                uow,
                None,
                "document.processed",
                resource_type="document_version",
                resource_id=version.id,
                details={
                    "document_id": str(document.id),
                    "version_no": version.version_no,
                    "status": version.status.value,
                    "pages": parsed.pages,
                    "chunks": len(chunks),
                    "language": language,
                    "risk_tier": risk.tier.value,
                    "risk_score": risk.score,
                    "pii": pii_summary.get("entities", {}),
                    "generation": self.generation,
                    "warnings": list(parsed.warnings)[:10],
                },
            )
            await self._emit(uow, document, version, "document.processed", {"status": version.status.value})
            await self._notifications.notify(
                uow,
                [version.uploaded_by_employee_id],
                "document.processed",
                {
                    "document_id": str(document.id),
                    "title": document.title,
                    "version_no": version.version_no,
                    "status": version.status.value,
                },
                dedupe_key=f"document.processed:{version.id}",
            )
            await uow.commit()
        return ProcessOutcome(version.id, version.status, len(chunks), parsed.pages, language)

    # --- document.reindex ------------------------------------------------------------------------------------
    async def reindex(self, tenant_id: UUID, version_id: UUID, *, rescore: bool = False) -> ProcessOutcome:
        """Rebuild chunks from the stored structure. `rescore` (M5 back-fill, `make rescan`) also records the risk
        assessment and holds back an ACTIVE version that is not low risk — unless a reviewer already released it."""
        context = worker_context(tenant_id)
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            known = await self._known_entities(uow)
            terms = await self._document_terms(uow)
            policy = await self._policies.policy_for(uow, tenant_id)
        if version.extracted_object_key is None:
            raise IngestionError(
                "no_extracted_structure", "the version has no stored structure; reprocess it"
            )
        parsed = blocks_from_json(json.loads(await self._storage.get_bytes(version.extracted_object_key)))
        drafts = chunk_document(
            parsed.blocks, title=document.title, count_tokens=self._tokenizer.count, rules=self._rules
        )
        contents = [d.content for d in drafts]
        structural = await self._structural_flags(version) if rescore else ()
        risk = await asyncio.to_thread(
            self._security.assess_blocks,
            contents,
            thresholds=self._policies.thresholds(policy),
            structural_flags=structural,
        )
        pii_findings = await self._detect_pii(contents, version.language, known, terms)
        vectors = await asyncio.to_thread(self._embedder.embed_passages, [d.embed_text for d in drafts])
        now = self._clock.now()
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            hold_event: VersionEvent | None = None
            retrievable = version.retrievable
            if rescore and version.status is VersionStatus.ACTIVE:
                released = any(
                    e.action is ReviewAction.RELEASE
                    for e in await uow.review_events.list_for_version(version.id)
                )
                if risk.tier is not RiskTier.LOW and not released:
                    hold_event = (
                        VersionEvent.SEND_TO_REVIEW
                        if risk.tier is RiskTier.MEDIUM
                        else VersionEvent.QUARANTINE
                    )
                # a failed first scan made the version unretrievable (`record_failure`); success restores it
                retrievable = hold_event is None
            chunks, pii_summary = await self._pseudonymised_chunks(
                uow,
                version,
                drafts,
                vectors,
                pii_findings,
                risk.chunk_flags,
                version.language,
                retrievable=retrievable,
            )
            await uow.chunks.replace_for_version(version.id, chunks)
            version.pii_summary = pii_summary
            version.index_generation = self.generation
            version.retrievable = retrievable
            version.updated_at = now
            if rescore:
                await self._record_findings(uow, version, risk)
                version.risk_score = risk.score
                version.risk_tier = risk.tier
                version.risk_findings = self._reviewer_findings(risk, chunks)
            if hold_event is not None:
                version.status = transition(version.status, hold_event)
                if document.active_version_id == version.id:
                    document.active_version_id = None
                    document.updated_at = now
                    await uow.documents.update(document, expected_version=document.version)
            await uow.document_versions.update(version)
            if hold_event is not None:
                await self._notify_reviewers(uow, document, version)
            await self._ensure_generation(uow, tenant_id, now)
            await self._audit.record(
                uow,
                None,
                "document.rescanned" if rescore else "document.reindexed",
                resource_type="document_version",
                resource_id=version.id,
                details={
                    "document_id": str(document.id),
                    "chunks": len(chunks),
                    "generation": self.generation,
                    "status": version.status.value,
                    "risk_tier": risk.tier.value,
                    "risk_score": risk.score,
                    "pii": pii_summary["entities"],
                },
            )
            await uow.commit()
        return ProcessOutcome(version.id, version.status, len(chunks), version.pages, version.language)

    async def record_failure(self, tenant_id: UUID, version_id: UUID, error: str, *, final: bool) -> None:
        """Keeps the last error on the version; after the final attempt the version becomes FAILED (doc 05 §11)."""
        context = worker_context(tenant_id)
        now = self._clock.now()
        async with self._uow(context) as uow:
            version, document = await self._load(uow, version_id)
            version.processing_error = error[:500]
            version.updated_at = now
            if final and can(version.status, VersionEvent.FAIL):
                version.status = transition(version.status, VersionEvent.FAIL)
                version.retrievable = False
            elif final and version.status is VersionStatus.ACTIVE and not version.pii_summary:
                # doc 08 §7.5: a version whose first PII scan failed stays out of retrieval until it succeeds
                version.retrievable = False
                await uow.chunks.set_retrievable(version.id, False)
            await uow.document_versions.update(version)
            if final:
                await self._audit.record(
                    uow,
                    None,
                    "document.failed",
                    resource_type="document_version",
                    resource_id=version.id,
                    details={
                        "document_id": str(document.id),
                        "version_no": version.version_no,
                        "error": error[:500],
                    },
                )
                await self._emit(uow, document, version, "document.failed", {"error": error[:500]})
                await self._notifications.notify(
                    uow,
                    [version.uploaded_by_employee_id],
                    "document.failed",
                    {
                        "document_id": str(document.id),
                        "title": document.title,
                        "version_no": version.version_no,
                        "error": error[:200],
                    },
                    dedupe_key=f"document.failed:{version.id}",
                )
            await uow.commit()

    # --- helpers ---------------------------------------------------------------------------------------------
    async def _parse(self, version: DocumentVersion) -> tuple[ParsedDocument, tuple[str, ...]]:
        """Parse the original; structural flags (active PDF content, DOCX macros) come from the raw bytes."""
        extension = MIME_EXTENSION.get(version.mime, "bin")
        with tempfile.TemporaryDirectory(prefix="ingest-") as directory:
            path = Path(directory) / f"original.{extension}"
            await self._storage.download_to(version.object_key, path)
            structural = await self._flags_of(path, version.mime)
            try:
                parsed = await asyncio.to_thread(
                    self._parser.parse, path, version.mime, max_pages=self._max_pages
                )
            except IngestionError:
                raise
            except Exception as exc:  # noqa: BLE001 - every parser failure becomes a stable reason
                raise IngestionError("parse_error", f"{type(exc).__name__}: {str(exc)[:300]}") from exc
            return parsed, structural

    async def _structural_flags(self, version: DocumentVersion) -> tuple[str, ...]:
        extension = MIME_EXTENSION.get(version.mime, "bin")
        with tempfile.TemporaryDirectory(prefix="rescan-") as directory:
            path = Path(directory) / f"original.{extension}"
            await self._storage.download_to(version.object_key, path)
            return await self._flags_of(path, version.mime)

    @staticmethod
    async def _flags_of(path: Path, mime: str) -> tuple[str, ...]:
        if mime == PDF:
            return tuple(pdf_structural_flags(await asyncio.to_thread(path.read_bytes)))
        if mime == DOCX and await asyncio.to_thread(docx_has_macros, path):
            return ("docx_macros",)
        return ()

    async def _notify_reviewers(self, uow: UnitOfWork, document: Document, version: DocumentVersion) -> None:
        """Doc 08 §8.2: reviewers of the document's category learn that a version was held back."""
        recipients: dict[UUID, None] = {}
        for role in sorted(REVIEWERS[document.category], key=lambda r: r.value):
            for employee in await uow.employees.list_active(role=role):
                recipients[employee.id] = None
        await self._notifications.notify(
            uow,
            list(recipients),
            "document.review_required",
            {
                "document_id": str(document.id),
                "title": document.title,
                "version_no": version.version_no,
                "status": version.status.value,
            },
            dedupe_key=f"document.review_required:{version.id}",
        )

    @staticmethod
    async def _known_entities(uow: UnitOfWork) -> list[KnownEntity]:
        """The tenant's active directory feeds the known-entity recogniser (doc 08 §7.1)."""
        return [
            KnownEntity(e.id, e.first_name, e.last_name, e.email, e.department_id)
            for e in await uow.employees.list_active()
        ]

    @staticmethod
    async def _document_terms(uow: UnitOfWork) -> tuple[str, ...]:
        """Organisational terms never pseudonymised in documents (M10, R-30): built-in HR terms + department names."""
        return organisational_terms(d.name for d in await uow.departments.list())

    async def _detect_pii(
        self, contents: list[str], language: str | None, known: list[KnownEntity], terms: tuple[str, ...]
    ) -> list[list[Finding]]:
        """Fail closed (doc 08 §7.5): missing keys or an engine failure never yield an ACTIVE version."""
        if not self._privacy.configured:
            raise IngestionError(
                "pii_not_configured", "vault keys are missing; documents cannot be made LLM-safe"
            )
        try:
            return await asyncio.to_thread(
                self._privacy.detect_many, contents, language=language, known=known, document_terms=terms
            )
        except Exception as exc:  # noqa: BLE001 - the reason is recorded on the version, never swallowed
            raise IngestionError("pii_scan_failed", f"{type(exc).__name__}: {str(exc)[:300]}") from exc

    async def _pseudonymised_chunks(
        self,
        uow: UnitOfWork,
        version: DocumentVersion,
        drafts: list[ChunkDraft],
        vectors: list[list[float]],
        findings: list[list[Finding]],
        flags: dict[int, tuple[str, ...]],
        language: str | None,
        *,
        retrievable: bool = False,
    ) -> tuple[list[Chunk], dict[str, Any]]:
        """Vault placeholders are allocated inside the indexing transaction so chunks and entries commit together."""
        safe_texts: list[str] = []
        has_pii: list[bool] = []
        counts: dict[str, int] = {}
        for draft, chunk_findings in zip(drafts, findings, strict=True):
            result = await self._privacy.apply(
                uow,
                version.tenant_id,
                draft.content,
                chunk_findings,
                source_kind="document_version",
                source_id=str(version.id),
            )
            safe_texts.append(str(result.safe))
            has_pii.append(result.has_pii)
            for entity, count in result.counts.items():
                counts[entity] = counts.get(entity, 0) + count
        chunks = self._chunks(
            version,
            drafts,
            vectors,
            tuple(safe_texts),
            tuple(has_pii),
            flags,
            language,
            retrievable=retrievable,
        )
        summary: dict[str, Any] = {
            "entities": dict(sorted(counts.items())),
            "chunks_with_pii": sum(has_pii),
            "engine": PII_ENGINE_LABEL,
        }
        return chunks, summary

    async def _record_findings(self, uow: UnitOfWork, version: DocumentVersion, risk: RiskAssessment) -> None:
        now = self._clock.now()
        records = [
            InjectionFinding(
                id=self._ids.new_id(),
                tenant_id=version.tenant_id,
                subject_type="document_version",
                subject_id=str(version.id),
                detector=str(finding["detector"]),
                score=float(finding["score"]),
                label=str(finding["family"]),
                excerpt_hash=excerpt_hash(str(finding.get("excerpt") or "")),
                details={"block": finding.get("block"), "classifier": self._security.classifier_version},
                created_at=now,
            )
            for finding in risk.findings
        ]
        if records:
            await uow.injection_findings.add_many(records)

    @staticmethod
    def _reviewer_findings(risk: RiskAssessment, chunks: list[Chunk]) -> list[dict[str, Any]]:
        """Findings shown to reviewers carry the pseudonymised chunk excerpt, never raw text (doc 08 §8.4)."""
        by_ordinal = {c.ordinal: c for c in chunks}
        out: list[dict[str, Any]] = []
        for finding in risk.findings:
            block = finding.get("block")
            chunk = by_ordinal.get(block) if isinstance(block, int) else None
            out.append(
                {
                    "detector": finding["detector"],
                    "family": finding["family"],
                    "score": finding["score"],
                    "chunk": block,
                    "excerpt": chunk.content_llm_safe[:200] if chunk else "",
                }
            )
        return out

    def _chunks(
        self,
        version: DocumentVersion,
        drafts: list[ChunkDraft],
        vectors: list[list[float]],
        llm_safe: tuple[str, ...],
        has_pii: tuple[bool, ...],
        flags: dict[int, tuple[str, ...]],
        language: str | None,
        *,
        retrievable: bool = False,
    ) -> list[Chunk]:
        if len(vectors) != len(drafts):
            raise IngestionError("embedding_mismatch", f"{len(vectors)} vectors for {len(drafts)} chunks")
        chunks: list[Chunk] = []
        for index, draft in enumerate(drafts):
            chunks.append(
                Chunk(
                    id=chunk_id_for(version.id, draft.ordinal),
                    tenant_id=version.tenant_id,
                    version_id=version.id,
                    ordinal=draft.ordinal,
                    chunk_type=draft.chunk_type,
                    heading_path=draft.heading_path,
                    page_start=draft.page_start,
                    page_end=draft.page_end,
                    language=self._language.detect(draft.content) or language,
                    content=draft.content,
                    content_llm_safe=llm_safe[index] if index < len(llm_safe) else draft.content,
                    token_count=draft.token_count,
                    embedding=list(vectors[index]),
                    embedding_model_id=self._embedder.model_id,
                    risk_flags=tuple(flags.get(index, ())),
                    has_pii=bool(has_pii[index]) if index < len(has_pii) else False,
                    retrievable=retrievable,
                )
            )
        return chunks

    async def _ensure_generation(self, uow: UnitOfWork, tenant_id: UUID, now: Any) -> None:
        if await uow.index_generations.get(self.generation) is None:
            await uow.index_generations.upsert(
                IndexGeneration(
                    tenant_id=tenant_id,
                    generation=self.generation,
                    embedding_model_id=self._embedder.model_id,
                    chunking_rules_version=self._rules.version,
                    status="active",
                    started_at=now,
                    completed_at=now,
                )
            )

    async def _emit(
        self, uow: UnitOfWork, document: Document, version: DocumentVersion, type: str, extra: dict[str, Any]
    ) -> None:
        await uow.integration_events.add(
            IntegrationEvent(
                id=self._ids.new_id(),
                tenant_id=document.tenant_id,
                type=type,
                payload={
                    "tenant_id": str(document.tenant_id),
                    "document_id": str(document.id),
                    "version_id": str(version.id),
                    "version_no": version.version_no,
                    **extra,
                },
                created_at=self._clock.now(),
            )
        )

    @staticmethod
    async def _load(uow: UnitOfWork, version_id: UUID) -> tuple[DocumentVersion, Document]:
        version = await uow.document_versions.get(version_id)
        if version is None:
            raise NotFoundError("version not found")
        document = await uow.documents.get(version.document_id)
        if document is None:
            raise NotFoundError("document not found")
        return version, document
