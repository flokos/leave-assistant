import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setAccessTokenGetter } from '../../auth/token'
import { installFetchMock, problemResponse } from '../../test-fetch'
import { renderWithProviders } from '../../test-utils'
import { SupportingDocuments } from './SupportingDocuments'

const doc = {
  id: 's1', request_id: 'r1', employee_id: 'e', original_filename: 'medical-note.png', mime: 'image/png', size_bytes: 41567,
  sha256: 'x', status: 'stored', uploaded_by_employee_id: 'e', created_at: '2026-11-05T09:00:00Z',
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setAccessTokenGetter(null)
})

describe('SupportingDocuments', () => {
  it('owner lists, attaches and downloads without a reason', async () => {
    const appended: [string, unknown][] = []
    vi.spyOn(FormData.prototype, 'append').mockImplementation(function (this: FormData, name: string, value: unknown) {
      appended.push([name, value])
    })
    const open = vi.fn()
    let uploaded = false
    const mock = installFetchMock({
      'GET /api/v1/leave/requests/r1/supporting-documents': () => (uploaded ? [doc] : []),
      'POST /api/v1/leave/requests/r1/supporting-documents': () => {
        uploaded = true
        return doc
      },
      'POST /api/v1/supporting-documents/s1/download-token': () => ({ token: 'tok', expires_at: '2026-11-05T09:01:00Z', url: '/api/v1/supporting-documents/download/tok' }),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<SupportingDocuments requestId="r1" requestStatus="APPROVED" isOwner isHr={false} open={open} />)
    expect(await screen.findByText('No supporting documents.')).toBeInTheDocument()
    const input = screen.getByTestId('supporting-upload').querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File([new Uint8Array([137, 80, 78, 71])], 'medical-note.png', { type: 'image/png' })] } })
    await waitFor(() => expect(mock.requestsTo('POST /api/v1/leave/requests/r1/supporting-documents')).toHaveLength(1))
    expect(appended.some(([name]) => name === 'file')).toBe(true)
    const table = await screen.findByTestId('supporting-table')
    expect(await within(table).findByText('medical-note.png')).toBeInTheDocument()
    fireEvent.click(within(table).getByTestId('supporting-download-s1'))
    await waitFor(() => expect(open).toHaveBeenCalledWith('/api/v1/supporting-documents/download/tok'))
    const tokenCall = mock.requestsTo('POST /api/v1/supporting-documents/s1/download-token')[0]
    expect(await tokenCall?.clone().json()).toEqual({ reason: null })
  })

  it('HR must give a reason to download and sees server problems', async () => {
    const open = vi.fn()
    const mock = installFetchMock({
      'GET /api/v1/leave/requests/r1/supporting-documents': () => [doc],
      'POST /api/v1/supporting-documents/s1/download-token': async (request) => {
        const body = (await request.clone().json()) as { reason: string | null }
        return body.reason ? { token: 'tok', expires_at: '2026-11-05T09:01:00Z', url: '/api/v1/supporting-documents/download/tok' } : problemResponse('validation-error', 422)
      },
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<SupportingDocuments requestId="r1" requestStatus="APPROVED" isOwner={false} isHr open={open} />)
    const table = await screen.findByTestId('supporting-table')
    fireEvent.click(await within(table).findByTestId('supporting-download-s1'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByTestId('supporting-reason'), { target: { value: 'verifying the certificate' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'OK' }))
    await waitFor(() => expect(open).toHaveBeenCalled())
    const call = mock.requestsTo('POST /api/v1/supporting-documents/s1/download-token')[0]
    expect(await call?.clone().json()).toEqual({ reason: 'verifying the certificate' })
    expect(within(table).getByTestId('supporting-delete-s1')).toBeInTheDocument()
  })

  it('renders nothing for managers', () => {
    const mock = installFetchMock({})
    renderWithProviders(<SupportingDocuments requestId="r1" requestStatus="APPROVED" isOwner={false} isHr={false} />)
    expect(screen.queryByTestId('supporting-documents')).toBeNull()
    expect(mock.calls).toHaveLength(0)
  })
})
