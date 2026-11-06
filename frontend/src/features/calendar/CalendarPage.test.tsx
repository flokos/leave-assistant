import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import type { AuthContextProps } from 'react-oidc-context'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setAccessTokenGetter } from '../../auth/token'
import { installFetchMock } from '../../test-fetch'
import { employeeMe, renderWithProviders } from '../../test-utils'
import CalendarPage from './CalendarPage'

const company = {
  id: 'c1',
  kind: 'company_shared',
  provider: 'mock',
  status: 'active',
  calendar: 'mock-cal-c1',
  scopes: [],
  last_success_at: '2026-11-06T09:00:00Z',
  last_error: null,
  created_at: '2026-11-06T08:00:00Z',
  updated_at: '2026-11-06T09:00:00Z',
}
const deadJob = {
  id: 'j1',
  request_id: 'r1',
  request_number: 'LR-2026-000004',
  connection_id: 'c1',
  connection_kind: 'company_shared',
  operation: 'create',
  status: 'dead',
  attempts: 5,
  next_attempt_at: null,
  error_class: 'permanent',
  last_error: '403 forbidden',
  created_at: '2026-11-06T08:00:00Z',
  updated_at: '2026-11-06T09:00:00Z',
  finished_at: '2026-11-06T09:00:00Z',
}

vi.mock('../../auth/token', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../auth/token')>()
  return { ...original, authEnabled: true }
})

const auth = { isAuthenticated: true, isLoading: false, user: { access_token: 't', profile: { sub: 's' } } as AuthContextProps['user'] }

afterEach(() => {
  vi.unstubAllGlobals()
  setAccessTokenGetter(null)
})

describe('CalendarPage', () => {
  it('company admin: both connection cards, retry of a failed job and reconciliation actions', async () => {
    const mock = installFetchMock({
      'GET /api/v1/me': () => ({ ...employeeMe, roles: ['employee', 'company_admin'] }),
      'GET /api/v1/calendar/connections': () => ({ provider: 'mock', items: [company] }),
      'GET /api/v1/calendar/sync-jobs': () => ({ items: [deadJob] }),
      'POST /api/v1/calendar/sync-jobs/j1/retry': () => ({ ...deadJob, status: 'pending', attempts: 0 }),
      'GET /api/v1/calendar/reconciliation': () => ({
        run_id: 'run1',
        run_at: '2026-11-06T04:00:00Z',
        counts: { orphaned: 1 },
        errors: {},
        items: [
          { id: 'i1', connection_id: 'c1', finding: 'orphaned', status: 'open', request_id: null, external_event_id: 'laxyz', action: null, details: {}, resolved_at: null },
        ],
      }),
      'POST /api/v1/calendar/reconciliation/items/i1/delete-orphan': () => ({ id: 'i1', status: 'resolved' }),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<CalendarPage />, { auth })
    const companyCard = await screen.findByTestId('connection-company_shared')
    expect(await within(companyCard).findByTestId('connection-status-company_shared')).toHaveTextContent('Active')
    expect(within(companyCard).queryByTestId('connect-company_shared')).not.toBeInTheDocument()
    const personal = screen.getByTestId('connection-personal')
    expect(within(personal).getByTestId('connect-personal')).toHaveTextContent('Connect Mock calendar')

    const jobs = screen.getByTestId('sync-jobs')
    expect(await within(jobs).findByText('LR-2026-000004')).toBeInTheDocument()
    expect(within(jobs).getByText('Failed')).toBeInTheDocument()
    expect(within(jobs).getByText('Permanent')).toBeInTheDocument()
    fireEvent.click(within(jobs).getByTestId('retry-j1'))
    await waitFor(() => expect(mock.requestsTo('POST /api/v1/calendar/sync-jobs/j1/retry')).toHaveLength(1))

    const reconciliation = screen.getByTestId('reconciliation')
    expect(await within(reconciliation).findByText('No approved request')).toBeInTheDocument()
    fireEvent.click(within(reconciliation).getByTestId('item-delete-orphan-i1'))
    await waitFor(() => expect(mock.requestsTo('POST /api/v1/calendar/reconciliation/items/i1/delete-orphan')).toHaveLength(1))
  })

  it('employee: personal card only, no retry and no reconciliation', async () => {
    installFetchMock({
      'GET /api/v1/me': () => employeeMe,
      'GET /api/v1/calendar/connections': () => ({ provider: 'google', items: [] }),
      'GET /api/v1/calendar/sync-jobs': () => ({ items: [deadJob] }),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<CalendarPage />, { auth })
    await waitFor(() => expect(screen.getByTestId('connect-personal')).toHaveTextContent('Connect Google Calendar'))
    expect(screen.queryByTestId('connection-company_shared')).not.toBeInTheDocument()
    expect(screen.queryByTestId('reconciliation')).not.toBeInTheDocument()
    expect(await screen.findByText('LR-2026-000004')).toBeInTheDocument()
    expect(screen.queryByTestId('retry-j1')).not.toBeInTheDocument()
  })

  it('shows the OAuth result from the callback redirect', async () => {
    installFetchMock({
      'GET /api/v1/me': () => employeeMe,
      'GET /api/v1/calendar/connections': () => ({ provider: 'mock', items: [] }),
      'GET /api/v1/calendar/sync-jobs': () => ({ items: [] }),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<CalendarPage />, { auth, route: '/calendar?status=error&reason=state_invalid' })
    expect(await screen.findByText(/expired or was already used|έληξε ή χρησιμοποιήθηκε ήδη/)).toBeInTheDocument()
  })
})
