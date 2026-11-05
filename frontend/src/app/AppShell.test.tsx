import { screen, within } from '@testing-library/react'
import type { AuthContextProps } from 'react-oidc-context'
import { Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setAccessTokenGetter } from '../auth/token'
import { installFetchMock } from '../test-fetch'
import { employeeMe, renderWithProviders } from '../test-utils'
import { AppShell } from './AppShell'

vi.mock('../auth/token', async (importOriginal) => {
  const original = await importOriginal<typeof import('../auth/token')>()
  return { ...original, authEnabled: true }
})

const notification = (id: string, read_at: string | null) => ({
  id,
  type: 'request.approved',
  params: { request_number: `LR-2026-00000${id}` },
  channels: ['in_app'],
  email_status: 'none',
  created_at: '2026-11-05T09:00:00Z',
  read_at,
})

afterEach(() => {
  vi.unstubAllGlobals()
  setAccessTokenGetter(null)
})

describe('AppShell', () => {
  it('renders the role menu, the user menu and the unread notification count', async () => {
    installFetchMock({
      'GET /api/v1/me': () => ({ ...employeeMe, roles: ['employee', 'manager'] }),
      'GET /api/v1/notifications': () => ({ items: [notification('1', null), notification('2', null)], next_cursor: null }),
    })
    setAccessTokenGetter(() => 'token-1')
    renderWithProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<div>home</div>} />
        </Route>
      </Routes>,
      { auth: { isAuthenticated: true, isLoading: false, user: { access_token: 'token-1', profile: { sub: 's', preferred_username: 'eleni' } } as AuthContextProps['user'] } },
    )
    const menu = await screen.findByTestId('workspace-menu')
    expect(within(menu).getByText('My leave')).toBeInTheDocument()
    expect(within(menu).getByText('Approvals')).toBeInTheDocument()
    expect(within(menu).queryByText('Employees')).not.toBeInTheDocument()
    expect(screen.getByTestId('user-menu')).toHaveTextContent('Ελένη Δημητρίου')
    expect(await screen.findByTestId('notifications-badge')).toHaveTextContent('2')
  })

  it('offers sign-in when there is no session', () => {
    installFetchMock({})
    renderWithProviders(
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<div>home</div>} />
        </Route>
      </Routes>,
      { auth: { isAuthenticated: false, isLoading: false } },
    )
    expect(screen.getByTestId('sign-in')).toBeInTheDocument()
    expect(screen.queryByTestId('workspace-menu')).not.toBeInTheDocument()
  })
})
