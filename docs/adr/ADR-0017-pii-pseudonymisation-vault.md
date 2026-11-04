# ADR-0017 — PII protection: Presidio detection, tenant-scoped pseudonym vault, authorised re-identification

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0008, ADR-0016, ADR-0022

## Context
Brief §9.1–§9.3: Presidio with custom Greek recognisers; pseudonymisation (`Alice Smith → EMPLOYEE_001`) with a local
mapping the LLM never receives; coverage of messages, documents, tool responses, history, outbound requests and
responses; re-identification only for authorised viewers; fail-closed for sensitive categories; detection is not a
guarantee.

## Problem
Make pseudonymisation consistent (the model must reason about the same placeholder across turns and sources),
reversible only for authorised readers, and robust to Greek morphology and NER misses.

## Decision
- Presidio Analyzer with spaCy engines for `en` (`en_core_web_lg`, MIT) and `el` (`el_core_news_md`, **CC BY-NC-SA 3.0**
  — acceptable for this non-commercial project; swap path via the `PiiEngine` port), custom Greek recognisers (AMKA,
  AFM, ID card, phones) and a deterministic known-entity recogniser built from the tenant's employee directory.
- Vault: tenant-stable placeholders `{TYPE}_{seq}` keyed by HMAC of the normalised value; originals encrypted;
  provenance recorded per placeholder occurrence.
- Type discipline: only `LlmSafeText` reaches prompt builders, state, traces and history.
- Re-identification in the API layer per the matrix in doc 08 §7.4; unknown placeholders become neutral labels.
- Fail closed: Presidio outage → no LLM call; supporting documents excluded by type.

## Alternatives considered
- Presidio Anonymizer's default redaction (`<PERSON>`): loses entity identity across mentions.
- Per-conversation placeholders: safer against linkage but breaks consistency across documents/tool results and
  evaluation reproducibility.
- Transformer-based NER (GLiNER/XLM-R) from the start: better recall potentially, heavier; kept as a swap path.

## Advantages
Consistent reasoning for the model; mapping never leaves the host; authorised readers see real names; auditable.

## Disadvantages
Residual risk of undetected PII (mitigated by the known-entity list and conservative thresholds); Greek NER recall
to be measured (R-07).

## Security implications
Vault keys are secrets; vault access is RLS-scoped; re-identification is a policy decision, not a lookup.

## Operational complexity
spaCy models in the image/volume; vault growth is bounded by distinct entities per tenant.

## Consequences
Evaluation includes PII leak checks on outbound payloads, checkpoints, logs and traces.
