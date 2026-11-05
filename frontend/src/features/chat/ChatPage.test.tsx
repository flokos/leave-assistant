import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import type { AuthContextProps } from 'react-oidc-context'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { setAccessTokenGetter } from '../../auth/token'
import { installFetchMock } from '../../test-fetch'
import { employeeMe, renderWithProviders } from '../../test-utils'
import ChatPage from './ChatPage'
import { SseParser } from './sse'

vi.mock('../../auth/token', async (importOriginal) => {
  const original = await importOriginal<typeof import('../../auth/token')>()
  return { ...original, authEnabled: true }
})

const auth = { isAuthenticated: true, isLoading: false, user: { access_token: 't', profile: { sub: 's' } } as AuthContextProps['user'] }
const citation = { n: 1, chunk_id: 'c1', document_id: 'd1', version_id: 'v1', version_no: 1, title: 'Κανονισμός Αδειών', heading_path: ['Κανονισμός Αδειών', '3. Κανονική άδεια'], page: 1 }
const conversation = { id: 'conv1', title: 'Πόσες μέρες άδειας;', created_at: '2026-11-05T09:00:00Z', last_activity_at: '2026-11-05T09:00:00Z' }

function sse(events: [string, unknown][]): Response {
  const body = events.map(([name, data]) => `event: ${name}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`).join('')
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setAccessTokenGetter(null)
})

describe('SseParser', () => {
  it('handles events split across chunks, CRLF and keep-alive comments', () => {
    const parser = new SseParser()
    expect(parser.feed('event: tok')).toEqual([])
    expect(parser.feed('en\r\ndata: {"text": "Γει')).toEqual([])
    expect(parser.feed('α"}\r\n\r\n: ping\r\n\r\nevent: done\ndata: {"outcome": "answered"}\n\n')).toEqual([
      { event: 'token', data: { text: 'Γεια' } },
      { event: 'done', data: { outcome: 'answered' } },
    ])
  })
})

describe('ChatPage', () => {
  it('survives browsers whose scrollIntoView returns a Promise', async () => {
    Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as unknown as Element['scrollIntoView']
    installFetchMock({
      'GET /api/v1/me': () => employeeMe,
      'GET /api/v1/chat/conversations': () => ({ items: [conversation], next_cursor: null }),
      'GET /api/v1/chat/conversations/conv1/messages': () => [],
    })
    setAccessTokenGetter(() => 't')
    const { unmount } = renderWithProviders(<ChatPage />, { auth })
    expect(await screen.findByTestId('conversation-item')).toBeInTheDocument()
    unmount()
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView
  })

  it('streams an answer with numbered citations and opens the cited chunk', async () => {
    let answered = false
    const mock = installFetchMock({
      'GET /api/v1/me': () => employeeMe,
      'GET /api/v1/chat/conversations': () => ({ items: answered ? [conversation] : [], next_cursor: null }),
      'POST /api/v1/chat/conversations': () => new Response(JSON.stringify(conversation), { status: 201, headers: { 'content-type': 'application/json' } }),
      'GET /api/v1/chat/conversations/conv1/messages': () =>
        answered
          ? [
              { id: 'm1', role: 'user', text: 'Πόσες μέρες άδειας;', citations: [], outcome: null, created_at: '2026-11-05T09:00:00Z' },
              { id: 'm2', role: 'assistant', text: 'Είκοσι εργάσιμες ημέρες [1].', citations: [citation], outcome: 'answered', created_at: '2026-11-05T09:00:01Z' },
            ]
          : [],
      'POST /api/v1/chat/conversations/conv1/messages': async (request) => {
        expect(await request.clone().json()).toEqual({ text: 'Πόσες μέρες άδειας;' })
        answered = true
        return sse([
          ['run_started', { run_id: 'r1', turn: 1 }],
          ['status', { stage: 'retrieving' }],
          ['token', { text: 'Είκοσι εργάσιμες ' }],
          ['token', { text: 'ημέρες [1].' }],
          ['citations', { items: [citation] }],
          ['done', { run_id: 'r1', outcome: 'answered', usage: {} }],
        ])
      },
      'GET /api/v1/documents/citations/c1': () => ({ ...citation, category: 'policy_general', chunk_type: 'text', page_start: 1, page_end: 1, language: 'el', content: 'Κάθε εργαζόμενος δικαιούται είκοσι εργάσιμες ημέρες.' }),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<ChatPage />, { auth })
    fireEvent.change(await screen.findByTestId('chat-input'), { target: { value: 'Πόσες μέρες άδειας;' } })
    fireEvent.click(screen.getByTestId('chat-send'))
    const pane = screen.getByTestId('chat-pane')
    await waitFor(() => expect(within(pane).getAllByTestId('message-assistant')[0]).toHaveTextContent('Είκοσι εργάσιμες ημέρες'))
    expect(mock.requestsTo('POST /api/v1/chat/conversations')).toHaveLength(1)
    const post = mock.requestsTo('POST /api/v1/chat/conversations/conv1/messages')[0]
    expect(post?.headers.get('Authorization')).toBe('Bearer t')
    expect(await within(pane).findByTestId('sources')).toHaveTextContent('[1] Κανονισμός Αδειών › 3. Κανονική άδεια')
    fireEvent.click(within(pane).getByTestId('cite-1'))
    expect(await screen.findByText('Κάθε εργαζόμενος δικαιούται είκοσι εργάσιμες ημέρες.')).toBeInTheDocument()
  })

  it('shows a problem-style error from the stream', async () => {
    installFetchMock({
      'GET /api/v1/me': () => employeeMe,
      'GET /api/v1/chat/conversations': () => ({ items: [conversation], next_cursor: null }),
      'GET /api/v1/chat/conversations/conv1/messages': () => [],
      'POST /api/v1/chat/conversations/conv1/messages': () => sse([['run_started', { run_id: 'r1', turn: 1 }], ['error', { type: 'chat/provider-unavailable', title: 'x', request_id: 'q' }]]),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<ChatPage />, { auth })
    await screen.findByTestId('conversation-item')
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Γεια' } })
    fireEvent.click(screen.getByTestId('chat-send'))
    expect(await screen.findByTestId('chat-error')).toHaveTextContent('The assistant is temporarily unavailable')
  })
})
