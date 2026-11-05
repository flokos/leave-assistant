import { vi } from 'vitest'

export type Handler = (request: Request) => unknown | Promise<unknown>

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

export function problemResponse(code: string, status: number, detail = '', extra: Record<string, unknown> = {}): Response {
  return new Response(
    JSON.stringify({ type: `urn:leave-assistant:problem:${code}`, title: code, status, detail, request_id: 'req-1', ...extra }),
    { status, headers: { 'content-type': 'application/problem+json' } },
  )
}

/** Routes keyed by `METHOD /path` (query string ignored); a handler may return a Response or a JSON body. */
export function installFetchMock(routes: Record<string, Handler>) {
  const calls: Request[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), 'http://localhost'), init)
    calls.push(request)
    const url = new URL(request.url)
    const key = `${request.method} ${url.pathname}`
    const handler = routes[key]
    if (!handler) return problemResponse('not-found', 404, key)
    const result = await handler(request)
    return result instanceof Response ? result : jsonResponse(result)
  })
  vi.stubGlobal('fetch', fn)
  return { fn, calls, requestsTo: (key: string) => calls.filter((r) => `${r.method} ${new URL(r.url).pathname}` === key) }
}
