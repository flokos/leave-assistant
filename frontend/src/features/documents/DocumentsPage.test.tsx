import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import type { AuthContextProps } from 'react-oidc-context'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setAccessTokenGetter } from '../../auth/token'
import { installFetchMock, problemResponse } from '../../test-fetch'
import { employeeMe, renderWithProviders } from '../../test-utils'
import DocumentsPage from './DocumentsPage'
import { uploadCategoriesFor } from './permissions'

vi.mock('../../auth/token', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../auth/token')>()
  return { ...original, authEnabled: true }
})

const version = (status: string, no = 1) => ({
  id: `v${no}`, document_id: 'd1', version_no: no, status, original_filename: 'kanonismos.pdf', mime: 'application/pdf', size_bytes: 1234,
  sha256: 'x', pages: 2, language: 'el', risk_tier: 'low', risk_score: 0, processing_error: null, processed_at: '2026-11-05T10:00:00Z',
  retrievable: status === 'ACTIVE', index_generation: 'g', uploaded_by_employee_id: 'e', attempts: 1, created_at: '2026-11-05T09:00:00Z', updated_at: '2026-11-05T10:00:00Z',
})
const documentOut = {
  id: 'd1', title: 'Κανονισμός Αδειών', category: 'policy_general', department_id: null, owner_employee_id: 'e', active_version_id: 'v1',
  language_hint: 'el', version: 1, created_at: '2026-11-05T09:00:00Z', updated_at: '2026-11-05T10:00:00Z', active_version: version('ACTIVE'), latest_version: version('ACTIVE'),
}
const hit = {
  chunk_id: 'c1', document_id: 'd1', version_id: 'v1', version_no: 1, title: 'Κανονισμός Αδειών', category: 'policy_general', chunk_type: 'text',
  heading_path: ['Κανονισμός Αδειών', '3.2 Μεταφορά υπολοίπου'], page_start: 1, page_end: 1, language: 'el', snippet: 'Έως πέντε ημέρες μεταφέρονται…', score: 0.03, vector_rank: 1, keyword_rank: 2,
}
const hrMe = { ...employeeMe, roles: ['employee', 'hr_admin'] }
const auth = { isAuthenticated: true, isLoading: false, user: { access_token: 't', profile: { sub: 's' } } as AuthContextProps['user'] }

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setAccessTokenGetter(null)
})

describe('DocumentsPage', () => {
  it('lists the library, searches and opens the detail drawer', async () => {
    installFetchMock({
      'GET /api/v1/me': () => hrMe,
      'GET /api/v1/documents': () => ({ items: [documentOut], next_cursor: null }),
      'GET /api/v1/documents/search': () => ({ query: 'μεταφορά', language: 'el', hits: [hit] }),
      'GET /api/v1/documents/d1': () => ({ ...documentOut, versions: [version('ACTIVE')], acl: [], can_manage: true, can_review: true }),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<DocumentsPage />, { auth })
    const table = await screen.findByTestId('documents-table')
    expect(await within(table).findByText('Κανονισμός Αδειών')).toBeInTheDocument()
    expect(within(table).getByText('General policy')).toBeInTheDocument()
    expect(within(table).getByTestId('status-ACTIVE')).toBeInTheDocument()
    expect(await screen.findByTestId('upload-document')).toBeInTheDocument()
    const search = screen.getByTestId('document-search').querySelector('input') as HTMLInputElement
    fireEvent.change(search, { target: { value: 'μεταφορά' } })
    fireEvent.keyDown(search, { key: 'Enter', code: 'Enter' })
    const results = await screen.findByTestId('search-results')
    expect(await within(results).findByText('3.2 Μεταφορά υπολοίπου')).toBeInTheDocument()
    expect(within(results).getByText('p. 1')).toBeInTheDocument()
    fireEvent.click(within(results).getByTestId('search-hit'))
    const detail = await screen.findByTestId('document-detail')
    expect(await within(detail).findByText('kanonismos.pdf')).toBeInTheDocument()
    expect(within(detail).getByTestId('retire-1')).toBeInTheDocument()
  })

  const uploadFixture = (first: () => unknown) => {
    const appended: [string, unknown][] = []
    vi.spyOn(FormData.prototype, 'append').mockImplementation(function (this: FormData, name: string, value: unknown) {
      appended.push([name, value])
    })
    // jsdom's File is not a Node Blob: the multipart body cannot be read back here, so the response is scripted
    // and the FormData parts are observed through the `append` spy
    const mock = installFetchMock({
      'GET /api/v1/me': () => hrMe,
      'GET /api/v1/documents': () => ({ items: [], next_cursor: null }),
      'POST /api/v1/documents': first,
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<DocumentsPage />, { auth })
    return { appended, mock }
  }

  const fillAndSubmit = async (title: string, file: File) => {
    fireEvent.click(await screen.findByTestId('upload-document'))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByTestId('document-title'), { target: { value: title } })
    const input = within(dialog).getByTestId('document-file').querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    const okButton = () => within(dialog).getByRole('button', { name: 'Upload document' })
    await waitFor(() => expect(okButton()).toBeEnabled())
    fireEvent.click(okButton())
    return dialog
  }

  it('shows the problem document when the upload is refused', async () => {
    uploadFixture(() => problemResponse('document/unsupported-type', 415, 'unsupported file type'))
    const dialog = await fillAndSubmit('image', new File(['\x89PNG'], 'image.png', { type: 'image/png' }))
    expect(await within(dialog).findByTestId('problem-alert')).toHaveTextContent('Unsupported file type')
    expect(within(dialog).getByTestId('problem-alert')).toHaveTextContent('Request id: req-1')
  })

  it('uploads as multipart with the idempotency key and the form parts', async () => {
    const { appended, mock } = uploadFixture(() => ({ ...documentOut, active_version: null, latest_version: version('UPLOADED') }))
    await fillAndSubmit('FAQ', new File(['# FAQ\n\ntext'], 'faq.md', { type: 'text/markdown' }))
    await waitFor(() => expect(mock.requestsTo('POST /api/v1/documents')).toHaveLength(1))
    const [sent] = mock.requestsTo('POST /api/v1/documents')
    expect(sent.headers.get('Idempotency-Key')).toBeTruthy()
    expect(sent.headers.get('Content-Type') ?? '').not.toContain('application/json')
    expect(appended.map(([name]) => name)).toEqual(['file', 'title', 'category'])
    expect(appended.find(([name]) => name === 'title')?.[1]).toBe('FAQ')
    expect(appended.find(([name]) => name === 'category')?.[1]).toBe('policy_general')
    expect((appended.find(([name]) => name === 'file')?.[1] as File).name).toBe('faq.md')
    await waitFor(() => expect(screen.queryByTestId('document-title')).not.toBeInTheDocument(), { timeout: 3000 }).catch(() => undefined)
  })

  it('derives upload categories from roles', () => {
    expect(uploadCategoriesFor(['employee'])).toEqual([])
    expect(uploadCategoriesFor(['employee', 'manager'])).toEqual(['department_notice'])
    expect(uploadCategoriesFor(['employee', 'hr_admin'])).toEqual(['policy_general', 'policy_hr_restricted'])
    expect(uploadCategoriesFor(['employee', 'company_admin'])).toEqual(['policy_general', 'admin_internal'])
  })
})
