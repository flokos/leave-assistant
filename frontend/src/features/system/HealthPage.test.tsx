import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '../../i18n'
import { HealthPage } from './HealthPage'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

describe('HealthPage', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders liveness data and readiness checks, including failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.endsWith('/healthz')
          ? jsonResponse({ status: 'ok', version: '0.1.0', app_env: 'test' })
          : jsonResponse(
              { status: 'not_ready', version: '0.1.0', checks: [{ name: 'postgres', ok: true, detail: '' }, { name: 'keycloak', ok: false, detail: 'HTTP 503' }] },
              503,
            ),
      ),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <HealthPage />
      </QueryClientProvider>,
    )
    expect(await screen.findByText('0.1.0')).toBeInTheDocument()
    expect(await screen.findByTestId('check-postgres')).toHaveTextContent('OK')
    expect(await screen.findByTestId('check-keycloak')).toHaveTextContent('HTTP 503')
  })
})
