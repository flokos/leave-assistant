# ADR-0004 — Keycloak: single realm with tenant groups and claims; platform and company administration separated

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0003

## Context
Brief §4.4 selects Keycloak with OIDC/OAuth2, JWT validation, RBAC and group/tenant-aware context; §4.5 requires
platform and company administration to be separate security domains; §4.6 requires dynamically creatable companies.

## Problem
Represent tenants and roles in Keycloak so that tenant creation is an API operation, the SPA keeps one OIDC
configuration, and platform administrators cannot automatically access tenant data.

## Decision
One realm `leave-assistant`. Realm roles `employee`, `manager`, `hr_admin`, `company_admin`, `platform_admin`,
`automation`. Tenant membership = group `/tenants/<slug>` with attribute `tenant_id`, emitted as the `tenant_id`
claim by a mapper; platform administrators live in `/platform` and have no `tenant_id`. Clients: `frontend-spa`
(public, PKCE), `backend-api` (audience), `n8n-automation` and `backend-admin` (service accounts). The backend
provisions users and groups through the Admin API; tokens with zero or multiple tenant claims are rejected for
tenant endpoints.

## Alternatives considered
- Realm per tenant: hard isolation of identities, but the SPA needs per-tenant OIDC discovery, Admin API work
  multiplies, and platform-level roles become awkward.
- Tenant as a client role or a custom attribute only: works, but groups give an auditable membership model and
  simple Admin API operations.

## Advantages
Dynamic tenants = group creation; one login page; straightforward JWKS validation; clear platform/tenant split.

## Disadvantages
All tenants share realm-wide settings (password policy, token lifetimes); acceptable for this project.

## Security implications
The tenant claim is the root of isolation: the mapper and group membership are provisioned only by the backend's
service account; audience checks prevent token reuse across clients; access tokens are short-lived.

## Operational complexity
Realm export committed and imported on start; Keycloak needs ~1 GB RAM (tuned heap).

## Consequences
`employee_roles` mirrors roles for queries, but authorization decisions always use token roles; a reconciliation
job reports drift.
