import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { AuthContext, type AuthContextProps } from 'react-oidc-context'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '../../i18n'
import { setAccessTokenGetter } from '../../auth/token'
import { MePanel } from './MePanel'

vi.mock('../../auth/token', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../auth/token')>()
  return { ...original, authEnabled: true }
})

const meResponse = {
  user_id: 'sub-1',
  username: 'eleni.dimitriou@aegean-logistics.example',
  display_name: 'Ελένη Δημητρίου',
  roles: ['employee'],
  locale: 'el',
  is_platform_admin: false,
  is_automation: false,
  tenant: {
    id: 't',
    slug: 'aegean',
    name: 'Aegean Logistics S.A.',
    default_locale: 'el',
    timezone: 'Europe/Athens',
  },
  employee: {
    id: 'e',
    employee_number: 'AEG-004',
    first_name: 'Ελένη',
    last_name: 'Δημητρίου',
    email: 'eleni.dimitriou@aegean-logistics.example',
    hire_date: '2021-06-01',
    status: 'active',
    department: { id: 'd', code: 'OPS', name: 'Λειτουργίες' },
  },
  feature_flags: {},
  pending_interrupt_count: 0,
}

function renderWithAuth(auth: Partial<AuthContextProps>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <AuthContext.Provider value={auth as AuthContextProps}>
      <QueryClientProvider client={client}>
        <MePanel />
      </QueryClientProvider>
    </AuthContext.Provider>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  setAccessTokenGetter(null)
})

describe('MePanel', () => {
  it('invites to sign in when there is no session', () => {
    renderWithAuth({ isAuthenticated: false, isLoading: false })
    expect(screen.getByTestId('me-panel')).toHaveTextContent('Sign in to see')
  })

  it('sends the bearer token and renders the principal', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify(meResponse), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    setAccessTokenGetter(() => 'token-123')
    renderWithAuth({
      isAuthenticated: true,
      isLoading: false,
      user: { access_token: 'token-123', profile: { sub: 'sub-1' } } as AuthContextProps['user'],
    })
    expect(await screen.findByText(/AEG-004/)).toBeInTheDocument()
    expect(screen.getByText('Aegean Logistics S.A. (aegean, Europe/Athens)')).toBeInTheDocument()
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-123')
  })

  it('shows the problem title on an API error', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          type: 'urn:leave-assistant:problem:tenant/suspended',
          title: 'Tenant suspended',
          status: 403,
          detail: 'tenant is suspended',
        }),
        {
          status: 403,
          headers: { 'content-type': 'application/problem+json' },
        },
      ),
    )
    renderWithAuth({
      isAuthenticated: true,
      isLoading: false,
      user: { access_token: 't', profile: { sub: 's' } } as AuthContextProps['user'],
    })
    expect(await screen.findByText('403 Tenant suspended')).toBeInTheDocument()
  })
})
