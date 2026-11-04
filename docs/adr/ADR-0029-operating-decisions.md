# ADR-0029 — Operating decisions: free tiers for fictional data, provider order as configuration, evaluation on the reference machine, Google project ownership, optional CI

**Status:** Accepted (2026-11-04) · **Related:** ADR-0010, ADR-0020, ADR-0022, ADR-0023, ADR-0025, ADR-0028

## Context
Four operating questions could not be settled from the brief alone: whether Gemini's free-tier data-use terms are
acceptable and whether a paid tier may be used; whether a larger machine is available for evaluation; who owns the
Google Cloud project for the Calendar integration; and whether hosted CI is acceptable. The owner asked for decisions
in the spirit of the brief.

## Problem
Decide these without introducing paid services or cloud dependencies, while keeping privacy, reproducibility and
honest evaluation.

## Decision
1. **Free tiers for fictional data only (Q-04).** The Gemini free tier's "used to improve our products" clause is
   accepted because all seed data is fictional, prompts are pseudonymised and sensitive categories never leave the
   host. The README and operations docs state that real personal or company data must not be used under free tiers.
   No paid tier. Gemini remains primary as the brief selects; `LLM_PROVIDER_ORDER` makes Groq-first a configuration
   change if Gemini quotas prove unusable.
2. **Evaluation on the reference machine (Q-05).** The 7.1 GB machine is treated as the only machine. An
   evaluation-session mode (`make eval-session`) stops `frontend` and `n8n`, runs the worker embedded in the API
   process, starts the `observability` profile with reduced ClickHouse/Valkey memory settings, and runs offline
   retrieval metrics before any Langfuse-backed live run. A larger host is a convenience only.
3. **Google project ownership (Q-06).** The developer's own Google account owns the Cloud project and the OAuth
   client in Testing status (≤ 100 test users, 7-day refresh tokens — handled by the parked-job/re-auth flow). Each
   tenant's company calendar is a secondary calendar created by the app in that account, isolated by calendar ID.
   The mock adapter is the default; the Google adapter is opt-in.
4. **CI (Q-13).** `make` targets are the contract and must pass locally; a GitHub Actions workflow mirrors them
   (free tier, no secrets, no live LLM calls) and is optional.
5. **Tenant bootstrap (Q-12).** The Platform Administrator creates the tenant and its first Company Administrator
   with a temporary password and forced change at first login; the credential never rests with the platform
   administrator; audited.
6. **Seed companies (Q-07).** Aegean Logistics S.A. (Piraeus, Greek-first, Greek public holidays 2026) and
   Brightwater Software Ltd. (Dublin, English-first, Irish public holidays 2026); all fictional.

## Alternatives considered
- Paid Gemini tier for stronger data terms: contradicts "avoid introducing paid services".
- Requiring a 16 GB host: contradicts "reproducible environment" on the machine actually available.
- A dedicated Google Workspace/organisation: cost and administration without benefit for a local demo.
- Mandatory hosted CI: a cloud dependency the brief does not want.

## Advantages
No cost, no cloud dependency, honest about data-use terms, evaluation reproducible on the stated hardware.

## Disadvantages
Reduced-memory Langfuse/ClickHouse settings slow evaluation sessions; 7-day re-authorisation during demos.

## Security implications
Free-tier data use is confined to fictional content by policy and documentation; OAuth tokens remain encrypted;
no secrets in CI.

## Operational complexity
One `make` target and a documented procedure for evaluation sessions; re-authorisation reminders via notifications.

## Consequences
R-01…R-05 in the risk register are re-baselined to these decisions; doc 02 §10 gains an "evaluation session" row;
doc 11 §8 and doc 12 M9 describe the session procedure.
