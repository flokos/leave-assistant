import i18n from 'i18next'
import { describe, expect, it } from 'vitest'
import '../../i18n'
import { notificationText } from './text'

const base = { id: 'n1', channels: ['in_app'], email_status: 'none', created_at: '2026-11-05T09:00:00Z', read_at: null }

describe('notificationText', () => {
  it('renders templates with status, leave type and role parameters localised', async () => {
    await i18n.changeLanguage('en')
    const t = i18n.t.bind(i18n) as (key: string, options?: Record<string, unknown>) => string
    expect(notificationText({ ...base, type: 'request.submitted', params: { request_number: 'LR-2026-000001', status: 'PENDING_MANAGER' } }, t)).toBe(
      'Your request LR-2026-000001 was submitted (Pending manager).',
    )
    expect(
      notificationText(
        { ...base, type: 'approval.pending', params: { request_number: 'LR-2026-000002', leave_type: 'annual', start_date: '2026-11-30', end_date: '2026-12-01', working_days: 2, step_no: 1, approver_role: 'manager' } },
        t,
      ),
    ).toBe('Request LR-2026-000002 (Annual leave, 2026-11-30 – 2026-12-01, 2 days) awaits your decision (step 1, Manager).')
  })

  it('falls back to the type for unknown templates and switches language', async () => {
    await i18n.changeLanguage('el')
    const t = i18n.t.bind(i18n) as (key: string, options?: Record<string, unknown>) => string
    expect(notificationText({ ...base, type: 'request.approved', params: { request_number: 'LR-2026-000003' } }, t)).toBe('Η αίτηση LR-2026-000003 εγκρίθηκε.')
    expect(notificationText({ ...base, type: 'something.new', params: {} }, t)).toBe('something.new')
    await i18n.changeLanguage('en')
  })
})
