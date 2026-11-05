import { afterEach, describe, expect, it } from 'vitest'
import { setAccessTokenGetter } from '../auth/token'
import { installFetchMock, problemResponse } from '../test-fetch'
import { api, ApiError, problemFrom, unwrap } from './client'

afterEach(() => setAccessTokenGetter(null))

describe('api client', () => {
  it('adds the bearer token and a fresh Idempotency-Key to state-changing calls only', async () => {
    const mock = installFetchMock({
      'GET /api/v1/leave/types': () => [],
      'POST /api/v1/leave/requests/validate': () => ({ valid: true }),
    })
    setAccessTokenGetter(() => 'token-1')
    await api.GET('/api/v1/leave/types')
    await api.POST('/api/v1/leave/requests/validate', { body: { leave_type: 'annual', start_date: '2026-11-30', end_date: '2026-12-01' } })
    await api.POST('/api/v1/leave/requests/validate', { body: { leave_type: 'annual', start_date: '2026-11-30', end_date: '2026-12-01' } })
    const [get, post1, post2] = mock.calls
    expect(get.headers.get('Authorization')).toBe('Bearer token-1')
    expect(get.headers.has('Idempotency-Key')).toBe(false)
    expect(post1.headers.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(post2.headers.get('Idempotency-Key')).not.toBe(post1.headers.get('Idempotency-Key'))
    expect(post1.headers.get('Accept')).toBe('application/json')
  })

  it('maps problem+json documents to ApiError with the short code', async () => {
    installFetchMock({
      'POST /api/v1/leave/requests': () => problemResponse('leave/overlap', 409, 'overlaps LR-2026-000003'),
    })
    const result = await api.POST('/api/v1/leave/requests', { body: { leave_type: 'annual', start_date: '2026-11-30', end_date: '2026-12-01' } })
    let caught: unknown
    try {
      unwrap(result)
    } catch (error) {
      caught = error
    }
    expect(caught).toBeInstanceOf(ApiError)
    const apiError = caught as ApiError
    expect(apiError.status).toBe(409)
    expect(apiError.code).toBe('leave/overlap')
    expect(apiError.problem.detail).toBe('overlaps LR-2026-000003')
    expect(apiError.problem.request_id).toBe('req-1')
  })

  it('falls back to a generic problem for non-problem bodies', () => {
    const response = new Response('<html/>', { status: 502, statusText: 'Bad Gateway' })
    const problem = problemFrom('<html/>', response)
    expect(problem).toMatchObject({ type: 'urn:leave-assistant:problem:http', status: 502, title: 'Bad Gateway' })
  })
})
