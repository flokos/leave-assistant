import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { installFetchMock } from '../../test-fetch'
import { leaveTypes, renderWithProviders } from '../../test-utils'
import ApprovalsPage from './ApprovalsPage'

const pending = [
  {
    fallback: false,
    reason: 'family trip',
    request: { id: 'r1', request_number: 'LR-2026-000007', employee_id: 'e4', leave_type: 'annual', start_date: '2026-11-30', end_date: '2026-12-01', working_days: 2, status: 'PENDING_MANAGER', created_at: '2026-11-05T08:00:00Z', decided_at: null, cancelled_at: null, submitted_at: '2026-11-05T08:00:00Z', version: 1 },
    requester: { id: 'e4', display_name: 'Ελένη Δημητρίου', employee_number: 'AEG-004', department_id: 'd' },
    step: { id: 's1', step_no: 1, approver_role: 'manager', status: 'pending', fallback: false, comment: null, decided_at: null, decided_by_employee_id: null, designated_employee_id: 'e3', version: 3 },
  },
  {
    fallback: true,
    reason: null,
    request: { id: 'r2', request_number: 'LR-2026-000008', employee_id: 'e5', leave_type: null, start_date: '2026-12-07', end_date: '2026-12-07', working_days: 1, status: 'PENDING_HR', created_at: '2026-11-05T08:00:00Z', decided_at: null, cancelled_at: null, submitted_at: '2026-11-05T08:00:00Z', version: 1 },
    requester: { id: 'e5', display_name: 'Κώστας Μιχαηλίδης', employee_number: 'AEG-005', department_id: 'd' },
    step: { id: 's2', step_no: 1, approver_role: 'hr', status: 'pending', fallback: true, comment: null, decided_at: null, decided_by_employee_id: null, designated_employee_id: null, version: 1 },
  },
]

afterEach(() => vi.unstubAllGlobals())

describe('ApprovalsPage', () => {
  it('lists pending steps with the HR-fallback flag and approves with the step version', async () => {
    const mock = installFetchMock({
      'GET /api/v1/approvals/pending': () => pending,
      'GET /api/v1/leave/types': () => leaveTypes,
      'POST /api/v1/approvals/s1/approve': () => ({ id: 'r1', request_number: 'LR-2026-000007', status: 'APPROVED', steps: [], events: [] }),
    })
    renderWithProviders(<ApprovalsPage />)
    const table = await screen.findByTestId('pending-table')
    expect(await within(table).findByText('LR-2026-000007')).toBeInTheDocument()
    expect(within(table).getAllByText('HR fallback')).toHaveLength(1)
    expect(within(table).getByText('family trip')).toBeInTheDocument()
    expect(within(table).getByText('Annual leave')).toBeInTheDocument()

    fireEvent.click(within(table).getAllByTestId('approve')[0])
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('Decision on LR-2026-000007')
    fireEvent.change(within(dialog).getByTestId('decision-comment'), { target: { value: 'enjoy' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(mock.requestsTo('POST /api/v1/approvals/s1/approve')).toHaveLength(1))
    const [call] = mock.requestsTo('POST /api/v1/approvals/s1/approve')
    expect(await call.json()).toEqual({ version: 3, comment: 'enjoy' })
    expect(call.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('requires a comment to reject', async () => {
    const mock = installFetchMock({
      'GET /api/v1/approvals/pending': () => pending,
      'GET /api/v1/leave/types': () => leaveTypes,
    })
    renderWithProviders(<ApprovalsPage />)
    const table = await screen.findByTestId('pending-table')
    fireEvent.click((await within(table).findAllByTestId('reject'))[0])
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reject' }))
    expect(await within(dialog).findByText('A comment is required when rejecting.')).toBeInTheDocument()
    expect(mock.requestsTo('POST /api/v1/approvals/s1/reject')).toHaveLength(0)
  })
})
