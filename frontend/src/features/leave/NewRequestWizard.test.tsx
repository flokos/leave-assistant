import { fireEvent, screen, waitFor } from '@testing-library/react'
import dayjs from 'dayjs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { installFetchMock } from '../../test-fetch'
import { leaveTypes, renderWithProviders } from '../../test-utils'
import { NewRequestWizard } from './NewRequestWizard'

const report = {
  valid: true,
  working_days: 2,
  excluded_dates: [],
  periods: [{ start_date: '2026-11-30', end_date: '2026-12-01' }],
  balance: { balance_days: 20, pending_days: 0, available_days: 20, after_days: 18 },
  approval_chain: ['manager'],
  auto_acknowledged: false,
  violations: [],
  warnings: [],
}
const initialValues = { leave_type: 'annual', dates: [dayjs('2026-11-30'), dayjs('2026-12-01')] as [dayjs.Dayjs, dayjs.Dayjs], reason: 'trip' }

afterEach(() => vi.unstubAllGlobals())

describe('NewRequestWizard', () => {
  it('shows the backend report and submits the validated request', async () => {
    const mock = installFetchMock({
      'GET /api/v1/leave/types': () => leaveTypes,
      'POST /api/v1/leave/requests/validate': () => report,
      'POST /api/v1/leave/requests': () => ({ id: 'r1', request_number: 'LR-2026-000099', status: 'PENDING_MANAGER', steps: [], events: [] }),
    })
    renderWithProviders(<NewRequestWizard initialValues={initialValues} />)
    fireEvent.click(await screen.findByTestId('validate'))
    const view = await screen.findByTestId('validation-report')
    expect(view).toHaveTextContent('Working days2')
    expect(view).toHaveTextContent('18 (Available: 20)')
    expect(view).toHaveTextContent('Manager')
    const [validateCall] = mock.requestsTo('POST /api/v1/leave/requests/validate')
    expect(await validateCall.json()).toEqual({ leave_type: 'annual', start_date: '2026-11-30', end_date: '2026-12-01', reason: 'trip' })

    fireEvent.click(screen.getByTestId('submit'))
    expect(await screen.findByTestId('submitted')).toHaveTextContent('Request LR-2026-000099 submitted — status Pending manager.')
    const [submitCall] = mock.requestsTo('POST /api/v1/leave/requests')
    expect(await submitCall.json()).toEqual({ leave_type: 'annual', start_date: '2026-11-30', end_date: '2026-12-01', reason: 'trip' })
    expect(submitCall.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('blocks submission when the backend reports violations', async () => {
    const mock = installFetchMock({
      'GET /api/v1/leave/types': () => leaveTypes,
      'POST /api/v1/leave/requests/validate': () => ({
        ...report,
        valid: false,
        violations: [{ code: 'leave/overlap', detail: 'overlaps LR-2026-000003', params: {} }],
        warnings: [{ code: 'leave/document-required', detail: '', params: {} }],
      }),
    })
    renderWithProviders(<NewRequestWizard initialValues={initialValues} />)
    fireEvent.click(await screen.findByTestId('validate'))
    expect(await screen.findByTestId('violation-leave/overlap')).toHaveTextContent('Overlapping request — overlaps LR-2026-000003')
    expect(screen.getByText(/A supporting document is required/)).toBeInTheDocument()
    expect(screen.getByTestId('submit')).toBeDisabled()
    await waitFor(() => expect(mock.requestsTo('POST /api/v1/leave/requests')).toHaveLength(0))
  })

  it('renders a problem document returned by validate', async () => {
    installFetchMock({
      'GET /api/v1/leave/types': () => leaveTypes,
      'POST /api/v1/leave/requests/validate': () =>
        new Response(JSON.stringify({ type: 'urn:leave-assistant:problem:validation-error', title: 'Validation error', status: 422, detail: 'end before start', request_id: 'req-9' }), {
          status: 422,
          headers: { 'content-type': 'application/problem+json' },
        }),
    })
    renderWithProviders(<NewRequestWizard initialValues={initialValues} />)
    fireEvent.click(await screen.findByTestId('validate'))
    const alert = await screen.findByTestId('problem-alert')
    expect(alert).toHaveTextContent('Validation error')
    expect(alert).toHaveTextContent('end before start')
    expect(alert).toHaveTextContent('Request id: req-9')
  })
})
