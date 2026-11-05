# ADR-0018 — Prompt-injection defence in depth and risk-tiered document handling

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0009, ADR-0017, ADR-0027

## Context
Brief §9.4–§9.5: treat uploaded and retrieved content as untrusted; layered controls (inspection, detection,
classification, risk scoring, allowlisting, argument validation, authorization, controlled execution, adversarial
tests); risk tiers low/medium/high with per-company thresholds and the stated reviewer roles; quarantined documents
must not be retrievable.

## Problem
No single detector is reliable, especially for Greek; the system must degrade safely and keep humans in the loop for
uncertain cases without blocking normal operation.

## Decision
Ten layers (doc 08 §8.1). Ingestion scoring combines bilingual heuristics, a licence-free similarity classifier
(e5 embeddings vs curated EL/EN injection exemplars) and an optional LLM judge for the medium band; tiers map to
`ACTIVE` with warning / `PENDING_REVIEW` / `QUARANTINED`, with thresholds in `security_policies` within platform-set
ranges. Runtime controls: data/instruction separation in prompts, role-based tool allowlists (read-only under medium
input risk), strict argument validation, backend authorization, human confirmation, and output verification
(citations, URLs, placeholders). User input is screened with the same detectors. An adversarial suite in both
languages runs in CI.

## Alternatives considered
- A single off-the-shelf classifier (Llama Prompt Guard 2, ProtectAI DeBERTa): useful but gated/English-centric
  and not Greek-trained; may be added as an extra signal later.
- LLM-only judging: costs quota and is itself injectable; used only as one signal in the medium band.
- Blocking all documents with any instruction-like text: unusable for real policies ("employees must…").

## Advantages
Multiple independent signals; conservative defaults with human review; structural controls (allowlists,
confirmation) make successful injections low-impact.

## Disadvantages
False positives create review work; thresholds need tuning with data (M5/M9).

## Security implications
Even a missed injection cannot execute business operations without a human confirmation bound to a server-side
proposal; it cannot read other tenants or restricted documents because retrieval is permission-filtered.

## Operational complexity
Exemplar set and heuristics versioned in the repo; review queue UI.

## Consequences
Security evaluation reports block rates per family and the false-positive rate on the benign corpus.

## Implementation note (M5, 2026-11-05)
Layers 1, 2 and 4 are implemented (ingestion scoring, tiers from `security_policies` with platform defaults and
ranges, user-input screening as a service); layers 3 and 5–9 arrive with the agent (M6/M7); layer 10 exists as the
EL/EN fixture suite (`make security-check`, `ml` calibration test). Deviation: the similarity classifier scores the
margin between the nearest injection exemplar and the nearest benign reference sentence (doc 08 §8.6) because
e5-small's raw similarities do not separate benign policy text from injections; the LLM judge stays an inert port
until the provider router exists. A `hidden_instruction` heuristic family (weight 0.5) covers the subtle-footnote
case in the medium band. See worklog M5.
