import { fireEvent, screen, waitFor } from '@testing-library/react'
import type { AuthContextProps } from 'react-oidc-context'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setAccessTokenGetter } from '../../auth/token'
import { installFetchMock, problemResponse } from '../../test-fetch'
import { employeeMe, renderWithProviders } from '../../test-utils'
import CompanySettingsPage from './CompanySettingsPage'
import { ExportAuditButton } from './ExportAuditButton'
import PlatformSettingsPage from './PlatformSettingsPage'
import SecurityPolicyPage from './SecurityPolicyPage'

vi.mock('../../auth/token', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../auth/token')>()
  return { ...original, authEnabled: true }
})

const auth = { isAuthenticated: true, isLoading: false, user: { access_token: 't', profile: { sub: 's' } } as AuthContextProps['user'] }
const adminMe = { ...employeeMe, roles: ['employee', 'company_admin'] }
const settings = { work_week: [0, 1, 2, 3, 4], max_upload_mb: 10, conversation_retention_days: 90, proposal_ttl_minutes: 15, allow_hr_fallback: true }
const policy = {
  tenant_id: 't1', risk_low_max: 0.3, risk_medium_max: 0.7, injection_llm_judge: false, user_input_block_threshold: 0.85,
  max_upload_mb: 25, allowed_mime: ['application/pdf'], updated_by: 'x', created_at: '2026-11-07T10:00:00Z', updated_at: '2026-11-07T10:00:00Z',
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setAccessTokenGetter(null)
})

describe('settings pages (M10)', () => {
  it('company settings load and save the editable fields only', async () => {
    let sent: Record<string, unknown> | null = null
    installFetchMock({
      'GET /api/v1/me': () => adminMe,
      'GET /api/v1/admin/settings': () => ({ name: 'Aegean Logistics', slug: 'aegean', default_locale: 'el', timezone: 'Europe/Athens', settings }),
      'PUT /api/v1/admin/settings': async (request) => {
        sent = (await request.json()) as Record<string, unknown>
        return { name: 'Aegean Logistics', slug: 'aegean', default_locale: 'el', timezone: 'Europe/Dublin', settings }
      },
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<CompanySettingsPage />, { auth })
    const timezone = await screen.findByTestId('settings-timezone')
    await waitFor(() => expect(timezone).toHaveValue('Europe/Athens'))
    expect(screen.getByText('Aegean Logistics')).toBeInTheDocument()
    fireEvent.change(timezone, { target: { value: 'Europe/Dublin' } })
    fireEvent.click(screen.getByTestId('settings-save'))
    await waitFor(() => expect(sent).not.toBeNull())
    expect(sent).toEqual({
      default_locale: 'el',
      timezone: 'Europe/Dublin',
      settings: { work_week: [0, 1, 2, 3, 4], conversation_retention_days: 90, proposal_ttl_minutes: 15, allow_hr_fallback: true },
    })
  })

  it('security policy shows the backend validation problem', async () => {
    installFetchMock({
      'GET /api/v1/me': () => adminMe,
      'GET /api/v1/admin/security-policy': () => policy,
      'PUT /api/v1/admin/security-policy': () =>
        problemResponse('validation-error', 422, 'invalid policy', { errors: [{ loc: ['body', 'risk_medium_max'], msg: 'must be greater than risk_low_max' }] }),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<SecurityPolicyPage />, { auth })
    await waitFor(() => expect(screen.getByTestId('security-risk-low')).toHaveValue('0.30'))
    fireEvent.click(screen.getByTestId('security-save'))
    expect(await screen.findByText(/must be greater than risk_low_max/)).toBeInTheDocument()
  })

  it('platform page edits the new-company defaults and the security defaults', async () => {
    const puts: string[] = []
    installFetchMock({
      'GET /api/v1/me': () => ({ ...employeeMe, roles: ['platform_admin'], tenant_id: null }),
      'GET /api/v1/platform/settings': () => ({ default_locale: 'en', timezone: 'Europe/Athens', tenant_settings: settings, updated_by: 'migration' }),
      'PUT /api/v1/platform/settings': (request) => {
        puts.push(new URL(request.url).pathname)
        return { default_locale: 'en', timezone: 'Europe/Athens', tenant_settings: settings, updated_by: 'p' }
      },
      'GET /api/v1/platform/security-defaults': () => ({ ...policy, threshold_min: 0.05, threshold_max: 0.95, max_upload_mb_limit: 50 }),
      'PUT /api/v1/platform/security-defaults': async (request) => {
        const body = (await request.json()) as Record<string, unknown>
        expect(body).not.toHaveProperty('allowed_mime')
        puts.push(new URL(request.url).pathname)
        return { ...policy, threshold_min: 0.05, threshold_max: 0.95, max_upload_mb_limit: 50 }
      },
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<PlatformSettingsPage />, { auth })
    await waitFor(() => expect(screen.getByTestId('security-threshold-max')).toHaveValue('0.95'))
    expect(screen.queryByTestId('security-mime')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('platform-settings-save'))
    fireEvent.click(screen.getByTestId('security-defaults-save'))
    await waitFor(() => expect(puts.sort()).toEqual(['/api/v1/platform/security-defaults', '/api/v1/platform/settings']))
  })

  it('the export button downloads the CSV with the current filters', async () => {
    const mock = installFetchMock({
      'GET /api/v1/admin/audit/export': () => new Response('﻿occurred_at,action\r\n', { headers: { 'content-type': 'text/csv' } }),
    })
    const created = vi.fn(() => 'blob:x')
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() }))
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    setAccessTokenGetter(() => 't')
    renderWithProviders(<ExportAuditButton scope="tenant" filters={{ action: 'audit.exported' }} />, { auth })
    fireEvent.click(screen.getByTestId('audit-export'))
    await waitFor(() => expect(click).toHaveBeenCalled())
    const request = mock.requestsTo('GET /api/v1/admin/audit/export')[0]
    expect(new URL(request.url).searchParams.get('action')).toBe('audit.exported')
    expect(created).toHaveBeenCalled()
  })
})
