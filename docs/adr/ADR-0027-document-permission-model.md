# ADR-0027 — Document categories, visibility and upload/review permissions

**Status:** Accepted (2026-11-04 — decided by applying the brief's principles, see doc 14 §4) · **Related:** ADR-0018, ADR-0028

## Context
Brief §4.5 defines what each role may access (managers cannot access confidential HR documents; HR manages HR
documents; company administrators manage general documents and review quarantined general documents; platform
administrators never access tenant documents). §9.5 asks for a secure, practical upload permission model.

## Problem
Define categories so that visibility is simple to reason about, uploads are limited to accountable roles, and reviews
go to the right people.

## Decision
Categories `policy_general` (all roles), `policy_hr_restricted` (HR + company admin), `department_notice`
(department members, their manager, HR, company admin), `admin_internal` (company admin), plus the non-indexed
`supporting_document`. Uploads: HR Administrators → HR categories and general policies; Company Administrators →
general and admin categories; Managers → department notices (P3); Employees → only supporting documents on their own
requests; Platform Administrators → none. Reviews: HR for HR categories, Company Administrator for general categories.
Optional ACLs can extend/narrow within the tenant but can never open HR-restricted content to employees or managers.
Visibility is enforced in SQL predicates and in the application policy.

## Alternatives considered
- Free-form per-document ACL only: flexible but error-prone; category defaults make the common case safe.
- Allowing employees to upload general documents: increases injection surface with no business need.

## Advantages
Few categories, clear defaults, auditable exceptions; matches the brief's role responsibilities.

## Disadvantages
Edge cases (cross-department documents) need ACLs.

## Security implications
Upload surface limited to administrators; reviewers match category ownership; retrieval predicates derived from the
same model.

## Operational complexity
None beyond admin UI screens.

## Consequences
Manager visibility was decided (Q-09): leave type visible to managers, sick-leave reasons/documents never; changes are
confined to the policy table and predicates.
