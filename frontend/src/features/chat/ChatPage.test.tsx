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

const card = {
  proposal_id: 'p1',
  kind: 'submit',
  status: 'pending',
  payload_hash: 'a'.repeat(64),
  expires_at: '2026-11-05T09:15:00Z',
  summary: {
    leave_type: 'annual',
    leave_type_name: { el: 'Κανονική άδεια', en: 'Annual leave' },
    start_date: '2026-12-14',
    end_date: '2026-12-18',
    working_days: 5,
    available_before: '20',
    available_after: '15',
    approval_chain: ['manager'],
    excluded: [],
  },
  actions: ['confirm', 'reject', 'edit'],
  outcome: {},
}

describe('Business chat (M7)', () => {
  it('shows tool chips and the confirmation card, then confirms through resume', async () => {
    let stage: 'empty' | 'card' | 'done' = 'empty'
    const mock = installFetchMock({
      'GET /api/v1/me': () => employeeMe,
      'GET /api/v1/leave/types': () => [],
      'GET /api/v1/chat/conversations': () => ({ items: [conversation], next_cursor: null }),
      'GET /api/v1/chat/conversations/conv1/messages': () =>
        stage === 'empty'
          ? []
          : [
              { id: 'm1', role: 'user', text: 'Five days from 14 December', citations: [], outcome: null, created_at: '2026-11-05T09:00:00Z', card: null },
              { id: 'm2', role: 'assistant', text: 'I have prepared the leave request.', citations: [], outcome: 'interrupted', created_at: '2026-11-05T09:00:01Z', card: stage === 'card' ? card : { ...card, status: 'executed', actions: [], outcome: { request_number: 'LR-2026-000042' } } },
              ...(stage === 'done' ? [{ id: 'm3', role: 'assistant', text: 'Request LR-2026-000042 was submitted.', citations: [], outcome: 'executed', created_at: '2026-11-05T09:00:05Z', card: null }] : []),
            ],
      'POST /api/v1/chat/conversations/conv1/messages': () => {
        stage = 'card'
        return sse([
          ['run_started', { run_id: 'r1', turn: 1 }],
          ['status', { stage: 'planning' }],
          ['tool_call', { name: 'get_my_balance', status: 'started' }],
          ['tool_call', { name: 'get_my_balance', status: 'ok' }],
          ['tool_call', { name: 'prepare_leave_request', status: 'ok' }],
          ['token', { text: 'I have prepared the leave request.' }],
          ['interrupt', { proposal_id: 'p1', kind: 'submit', card, expires_at: card.expires_at }],
        ])
      },
      'POST /api/v1/chat/conversations/conv1/resume': async (request) => {
        expect(await request.clone().json()).toEqual({ proposal_id: 'p1', decision: 'confirm', payload_hash: card.payload_hash })
        stage = 'done'
        return sse([
          ['run_started', { run_id: 'r2', turn: 2 }],
          ['status', { stage: 'executing' }],
          ['token', { text: 'Request LR-2026-000042 was submitted.' }],
          ['done', { run_id: 'r2', outcome: 'executed', usage: {} }],
        ])
      },
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<ChatPage />, { auth })
    await screen.findByTestId('conversation-item')
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Five days from 14 December' } })
    fireEvent.click(screen.getByTestId('chat-send'))
    const proposal = await screen.findByTestId('proposal-card')
    expect(proposal).toHaveTextContent('2026-12-14 – 2026-12-18')
    expect(proposal).toHaveTextContent('20 → 15')
    expect(within(proposal).getByTestId('card-status')).toHaveTextContent('Waiting for you')
    fireEvent.click(within(proposal).getByTestId('card-confirm'))
    await waitFor(() => expect(mock.requestsTo('POST /api/v1/chat/conversations/conv1/resume')).toHaveLength(1))
    await waitFor(() => expect(screen.getByTestId('card-status')).toHaveTextContent('Done'))
    expect(screen.getByText('Request LR-2026-000042 was submitted.')).toBeInTheDocument()
    expect(screen.queryByTestId('card-confirm')).not.toBeInTheDocument()
  })

  it('shows a decided card from history without actions', async () => {
    installFetchMock({
      'GET /api/v1/me': () => employeeMe,
      'GET /api/v1/leave/types': () => [],
      'GET /api/v1/chat/conversations': () => ({ items: [conversation], next_cursor: null }),
      'GET /api/v1/chat/conversations/conv1/messages': () => [
        { id: 'm2', role: 'assistant', text: 'Prepared.', citations: [], outcome: 'interrupted', created_at: '2026-11-05T09:00:01Z', card: { ...card, status: 'expired', actions: [] } },
      ],
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<ChatPage />, { auth })
    expect(await screen.findByTestId('card-status')).toHaveTextContent('Expired')
    expect(screen.queryByTestId('card-confirm')).not.toBeInTheDocument()
  })
})

describe('New conversation', () => {
  it('stays new instead of falling back to the latest conversation', async () => {
    const mock = installFetchMock({
      'GET /api/v1/me': () => employeeMe,
      'GET /api/v1/chat/conversations': () => ({ items: [conversation], next_cursor: null }),
      'GET /api/v1/chat/conversations/conv1/messages': () => [],
      'POST /api/v1/chat/conversations': () => new Response(JSON.stringify({ ...conversation, id: 'conv2' }), { status: 201, headers: { 'content-type': 'application/json' } }),
      'GET /api/v1/chat/conversations/conv2/messages': () => [],
      'POST /api/v1/chat/conversations/conv2/messages': () => sse([['done', { run_id: 'r', outcome: 'answered', usage: {} }]]),
    })
    setAccessTokenGetter(() => 't')
    renderWithProviders(<ChatPage />, { auth })
    await screen.findByTestId('conversation-item')
    fireEvent.click(screen.getByTestId('new-conversation'))
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Γεια' } })
    fireEvent.click(screen.getByTestId('chat-send'))
    await waitFor(() => expect(mock.requestsTo('POST /api/v1/chat/conversations/conv2/messages')).toHaveLength(1))
    expect(mock.requestsTo('POST /api/v1/chat/conversations/conv1/messages')).toHaveLength(0)
  })
})
