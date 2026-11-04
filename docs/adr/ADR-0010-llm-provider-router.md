# ADR-0010 — LLM provider router: Gemini primary, Groq failover, error classification, health tracking

**Status:** Accepted (baseline v0.1, 2026-11-04) · **Related:** ADR-0007, ADR-0023

## Context
Brief §4.2 selects Gemini (primary) and Groq (alternative) with provider abstraction, automatic failover, bounded
retries, timeouts, health tracking, selection logging and env configuration; invalid outputs, security violations
and authorization failures must not trigger failover. Claude is not a runtime dependency.

## Problem
Make two different providers interchangeable mid-conversation without changing behaviour, under free-tier quotas
(low RPD on Gemini, 8–12K TPM on Groq), and keep failures classified.

## Decision
`LLMProvider` port with adapters built on `langchain-google-genai` and `langchain-groq` (uniform tool binding and
structured output), a `FakeProvider` for tests, and a `ProviderRouter` implementing: timeouts (25 s; 10 s to first
token), bounded retries with jitter, failover only on transient classes (429/5xx/timeouts/network), one same-provider
repair attempt on invalid structured output, no retry on content blocks or authorization/security errors, a per
provider+model circuit breaker, and logging/tracing of every selection. Model IDs and the ordered policy come from
environment variables; prompts avoid provider-specific features; every call respects a 3,500-token input budget.

## Alternatives considered
- LangChain `with_fallbacks` alone: no health tracking, no error-class discrimination.
- Native SDKs without LangChain: fewer layers, but tool-calling schemas and streaming differ per provider.
- A gateway proxy (LiteLLM/OpenRouter): extra service/cloud dependency; not needed for two providers.

## Advantages
Explicit, testable failover semantics; provider differences contained in adapters; quotas respected by design.

## Disadvantages
Failover can change answer style/quality; evaluation runs on both providers to characterise it.

## Security implications
Only pseudonymised, budgeted content leaves the host; API keys live in env; failures never leak provider payloads
to users.

## Operational complexity
Health visible in `/platform/health`; provider events table for audit and digests.

## Consequences
Free-tier quota numbers must be read from AI Studio/Groq console at M6 and encoded as configuration (R-03, R-15).
