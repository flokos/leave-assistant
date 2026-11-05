// Minimal fetch wrapper with bearer injection. Response/request types come from the OpenAPI snapshot
// (docs/api/openapi.json → src/api/schema.d.ts via `npm run api:types`).
import { currentAccessToken } from '../auth/token'
import type { components } from './schema'

export const API_BASE = '/api/v1'

export type Schemas = components['schemas']
export type MeResponse = Schemas['MeResponse']
export type Healthz = { status: string; version: string; app_env: string }
export type ReadinessCheck = { name: string; ok: boolean; detail: string }
export type Readyz = { status: 'ready' | 'not_ready'; version: string; checks: ReadinessCheck[] }

export interface Problem {
  type: string
  title: string
  status: number
  detail: string
  instance?: string
  request_id?: string
  errors?: { loc: string[]; msg: string; type?: string }[]
}

export class ApiError extends Error {
  constructor(
    public readonly problem: Problem,
    public readonly status: number,
  ) {
    super(problem.title)
  }
}

export function authHeaders(): Record<string, string> {
  const token = currentAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function toError(response: Response): Promise<ApiError> {
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/problem+json')) {
    return new ApiError((await response.json()) as Problem, response.status)
  }
  return new ApiError(
    {
      type: 'urn:leave-assistant:problem:http',
      title: response.statusText,
      status: response.status,
      detail: '',
    },
    response.status,
  )
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...authHeaders(), ...init?.headers },
  })
  if (!response.ok) throw await toError(response)
  return (await response.json()) as T
}

export const getHealthz = (): Promise<Healthz> => apiGet<Healthz>('/healthz')
export const getMe = (): Promise<MeResponse> => apiGet<MeResponse>('/me')

// readyz answers 503 with the same JSON body when a check fails; surface it as data, not as an error.
export async function getReadyz(): Promise<Readyz> {
  const response = await fetch(`${API_BASE}/readyz`, { headers: { Accept: 'application/json' } })
  if (response.status !== 200 && response.status !== 503) throw await toError(response)
  return (await response.json()) as Readyz
}
