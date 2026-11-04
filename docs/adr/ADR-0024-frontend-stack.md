# ADR-0024 — Frontend: React 19 + TypeScript + Vite + Ant Design + react-oidc-context; no business logic

**Status:** Accepted (baseline v0.1, 2026-11-04)

## Context
Brief §11 selects React + TypeScript (over Streamlit), a professional enterprise interface with five role
workspaces, Greek and English, and no business logic that belongs in FastAPI.

## Problem
Choose a UI foundation that delivers dense enterprise screens (tables, forms, review queues) quickly, supports
Greek locale out of the box, and keeps the frontend a pure renderer of backend decisions.

## Decision
Vite 8 + React 19 + TypeScript; **Ant Design 6** (MIT; `el_GR`/`en_US` locales; peer React ≥18); `react-oidc-context`
+ `oidc-client-ts` for PKCE login and silent renew; TanStack Query for server state; `react-i18next` for i18n; a
client generated from the backend OpenAPI document (`openapi-typescript`); SSE consumption via `fetch` streams.
Role-based routing is cosmetic; every decision is re-checked by the backend. Node 24 LTS for builds (Vite requires
≥ 20.19/22.12); production image = nginx serving the build and proxying `/api`.

## Alternatives considered
- MUI: comparable; Ant Design's table/form density suits admin-heavy screens better.
- shadcn/ui + Tailwind: flexible, more assembly work for enterprise widgets.
- Next.js: SSR not needed; adds a server runtime.

## Advantages
Fast delivery of admin UIs; typed API client prevents drift; one OIDC configuration.

## Disadvantages
Ant Design's bundle size (acceptable locally); design customisation requires theme tokens.

## Security implications
Tokens in memory (no localStorage persistence of access tokens); same-origin API; CSP headers from nginx.

## Operational complexity
Build stage in Docker; no runtime Node process.

## Consequences
Business rules never move to the client; validation UX relies on the backend's `ValidationReport`.
