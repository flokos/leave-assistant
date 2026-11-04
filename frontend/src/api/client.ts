// Minimal fetch wrapper. The typed client generated from OpenAPI (openapi-typescript) is added when endpoints exist (M1+).
export const API_BASE = '/api/v1'

export interface Problem {
  type: string
  title: string
  status: number
  detail: string
  instance?: string
  request_id?: string
}

export class ApiError extends Error {
  constructor(
    public readonly problem: Problem,
    public readonly status: number,
  ) {
    super(problem.title)
  }
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { Accept: 'application/json', ...init?.headers } })
  const contentType = response.headers.get('content-type') ?? ''
  if (!response.ok && contentType.includes('application/problem+json')) {
    throw new ApiError((await response.json()) as Problem, response.status)
  }
  if (!response.ok) {
    throw new ApiError({ type: 'urn:leave-assistant:problem:http', title: response.statusText, status: response.status, detail: '' }, response.status)
  }
  return (await response.json()) as T
}

export interface Healthz {
  status: string
  version: string
  app_env: string
}

export interface ReadinessCheck {
  name: string
  ok: boolean
  detail: string
}

export interface Readyz {
  status: 'ready' | 'not_ready'
  version: string
  checks: ReadinessCheck[]
}

export const getHealthz = (): Promise<Healthz> => apiGet<Healthz>('/healthz')

// readyz answers 503 with the same JSON body when a check fails; surface it as data, not as an error.
export async function getReadyz(): Promise<Readyz> {
  const response = await fetch(`${API_BASE}/readyz`, { headers: { Accept: 'application/json' } })
  if (response.status !== 200 && response.status !== 503) {
    throw new ApiError({ type: 'urn:leave-assistant:problem:http', title: response.statusText, status: response.status, detail: '' }, response.status)
  }
  return (await response.json()) as Readyz
}
