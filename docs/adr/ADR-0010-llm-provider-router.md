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

## Implementation note (M6, 2026-11-05)
Implemented in M6 as decided: `ChatModel` port, Gemini (`json_schema` structured output) and Groq (tool-calling structured output) adapters with SDK retries disabled, `FakeChatModel`, `ProviderRouter` (25 s timeout, two jittered retries on transient errors, failover in `LLM_PROVIDER_ORDER`, one same-provider repair on invalid output then surface, no retry on content blocks, per provider+model circuit breaker: 3 consecutive failures → open 60 s, fatal → open 10 min, half-open probe), 3,500-token budget asserted before every call (e5 tokenizer × 1.15), every non-trivial event in `provider_events`. Default models (verified 2026-11-05): Gemini `gemini-3.5-flash-lite` (fast) / `gemini-3.6-flash` (answers); Groq `llama-3.1-8b-instant` / `llama-3.3-70b-versatile`. See worklog M6.

**Amendment 2026-11-06 (worklog M6 addendum):** the user's Groq signup was refused, so **Mistral** (free "Experiment"
plan, `langchain-mistralai`, strict JSON-schema output) became the second provider: `LLM_PROVIDER_ORDER` defaults to
`gemini,mistral,groq`, and providers without a key are skipped. Models on the free plan: `ministral-8b-2512` (fast)
and `ministral-14b-2512` (answers); `mistral-small`/`-medium` are not available on it, and `mistral-large-4` always
reasons. The live failover drill (Gemini forced to time out) answered every question from Mistral and opened Gemini's
circuits after three failed turns. Groq remains implemented but untested live.

