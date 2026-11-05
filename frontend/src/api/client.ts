// Typed API client generated from the OpenAPI snapshot (docs/api/openapi.json → src/api/schema.d.ts via `make contract`).
// Middleware adds the bearer token, a fresh Idempotency-Key on every state-changing call, and maps RFC 9457
// problem documents to ApiError. The frontend never evaluates business rules (FR-UI-04).
import createClient, { type Middleware } from 'openapi-fetch'
import { currentAccessToken } from '../auth/token'
import type { components, paths } from './schema'

export type Schemas = components['schemas']
export type MeResponse = Schemas['MeResponse']
export type Healthz = { status: string; version: string; app_env: string }
export type ReadinessCheck = { name: string; ok: boolean; detail: string }
export type Readyz = { status: 'ready' | 'not_ready'; version: string; checks: ReadinessCheck[] }

export interface ProblemError {
  loc?: string[]
  msg?: string
  type?: string
  code?: string
  params?: Record<string, unknown>
}

export interface Problem {
  type: string
  title: string
  status: number
  detail: string
  instance?: string
  request_id?: string | null
  errors?: ProblemError[]
}

export class ApiError extends Error {
  constructor(
    public readonly problem: Problem,
    public readonly status: number,
  ) {
    super(problem.title)
  }

  /** The problem type without the `urn:leave-assistant:problem:` prefix, e.g. `leave/overlap`. */
  get code(): string {
    return this.problem.type.replace(/^urn:leave-assistant:problem:/, '')
  }
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
let unauthorizedHandler: (() => void) | null = null

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

export function problemFrom(body: unknown, response: Response): Problem {
  if (body && typeof body === 'object' && 'type' in body && 'status' in body) return body as Problem
  return {
    type: 'urn:leave-assistant:problem:http',
    title: response.statusText || `HTTP ${response.status}`,
    status: response.status,
    detail: '',
  }
}

const middleware: Middleware = {
  async onRequest({ request }) {
    const token = currentAccessToken()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    if (MUTATING.has(request.method) && !request.headers.has('Idempotency-Key')) {
      request.headers.set('Idempotency-Key', crypto.randomUUID())
    }
    request.headers.set('Accept', 'application/json')
    return request
  },
  async onResponse({ response }) {
    if (response.status === 401) unauthorizedHandler?.()
    return response
  },
}

// Same-origin base URL (nginx proxies /api/ to the backend). `fetch` is resolved per call so tests can stub it.
const origin = typeof window !== 'undefined' ? window.location.origin : ''
export const api = createClient<paths>({ baseUrl: origin, fetch: (input) => globalThis.fetch(input) })
api.use(middleware)

/** Turns an openapi-fetch result into data or throws ApiError. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(problemFrom(result.error, result.response), result.response.status)
  }
  return result.data as T
}

export const API_BASE = '/api/v1'

export async function getHealthz(): Promise<Healthz> {
  const response = await fetch(`${API_BASE}/healthz`, { headers: { Accept: 'application/json' } })
  if (!response.ok) throw new ApiError(problemFrom(await response.json().catch(() => null), response), response.status)
  return (await response.json()) as Healthz
}

// readyz answers 503 with the same JSON body when a check fails; surface it as data, not as an error.
export async function getReadyz(): Promise<Readyz> {
  const response = await fetch(`${API_BASE}/readyz`, { headers: { Accept: 'application/json' } })
  if (response.status !== 200 && response.status !== 503) {
    throw new ApiError(problemFrom(await response.json().catch(() => null), response), response.status)
  }
  return (await response.json()) as Readyz
}

export const getMe = async (): Promise<MeResponse> => unwrap(await api.GET('/api/v1/me'))
