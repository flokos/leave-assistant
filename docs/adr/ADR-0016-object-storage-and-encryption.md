# ADR-0016 — Object storage port over SeaweedFS S3; tenant key layout; envelope encryption for supporting documents

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0003, ADR-0017

## Context
Brief §7.2 selects SeaweedFS via its S3 interface with a storage abstraction, separation of originals and extracted
content, versioning and secure tenant-aware access; §5.6 requires supporting medical documents to be accessible only to
authorised HR staff, audited, and never sent to external LLM providers or Langfuse.

## Problem
Keep storage swappable, enforce tenant separation at the key level, and protect the most sensitive artefacts even if
the object store is read directly.

## Decision
`DocumentStorage` port with an S3 adapter (boto3, path-style, single bucket) and a local-filesystem adapter for tests.
Keys: `tenants/{tenant_id}/documents/{document_id}/{version}/original.{ext}`, `…/extracted.json`, and
`tenants/{tenant_id}/supporting/{request_id}/{id}`. Supporting documents are encrypted by the backend before upload
(AES-256-GCM with a per-file DEK wrapped by `APP_KEK`); downloads stream through the API with a single-use 60-second
token after authorization and audit. Browsers never receive S3 URLs. SeaweedFS also serves as Langfuse's blob store
(separate bucket and credentials).

## Alternatives considered
- MinIO: common choice, but the brief selects SeaweedFS; both are S3-compatible so the adapter is identical.
- Database `bytea` storage: simple, but bloats PostgreSQL and complicates backups.
- Server-side encryption in SeaweedFS only: protects disks, not an attacker with S3 credentials.

## Advantages
Swappable backend; tenant-prefixed keys simplify auditing and deletion; sensitive files unreadable without the KEK.

## Disadvantages
Key management responsibility (rotation procedure documented); encrypted objects cannot be previewed by the store.

## Security implications
S3 credentials alone do not expose supporting documents; access is centralised in the API with audit events.

## Operational complexity
One SeaweedFS process (`weed server -s3`); bucket initialisation script; backup via volume snapshot.

## Consequences
Deletion for retention removes objects and keeps tombstones; Chroma/pgvector never store supporting-document data.
