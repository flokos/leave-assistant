// Server-sent events over fetch (EventSource cannot POST): the chat answer stream of doc 09 §3.
import { ApiError, problemFrom } from '../../api/client'
import { currentAccessToken } from '../../auth/token'

export interface ChatStreamEvent {
  event: string
  data: Record<string, unknown>
}

/** Incremental parser: feed text chunks, get complete events (blank-line separated blocks). */
export class SseParser {
  private buffer = ''

  feed(chunk: string): ChatStreamEvent[] {
    this.buffer += chunk.replace(/\r\n/g, '\n')
    const events: ChatStreamEvent[] = []
    let index = this.buffer.indexOf('\n\n')
    while (index >= 0) {
      const block = this.buffer.slice(0, index)
      this.buffer = this.buffer.slice(index + 2)
      let name = 'message'
      const data: string[] = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) name = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
      }
      if (data.length) {
        try {
          events.push({ event: name, data: JSON.parse(data.join('\n')) as Record<string, unknown> })
        } catch {
          // keep-alive comments or malformed data are ignored
        }
      }
      index = this.buffer.indexOf('\n\n')
    }
    return events
  }
}

/** POSTs a message and calls `onEvent` for every SSE event; resolves when the stream ends. */
export async function streamMessage(
  conversationId: string,
  text: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'text/event-stream' }
  const token = currentAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const response = await globalThis.fetch(`${origin}/api/v1/chat/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ text }),
    signal,
  })
  if (!response.ok || !response.body) {
    const body: unknown = await response.json().catch(() => undefined)
    throw new ApiError(problemFrom(body, response), response.status)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SseParser()
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    for (const event of parser.feed(decoder.decode(value, { stream: true }))) onEvent(event)
  }
  for (const event of parser.feed('\n\n')) onEvent(event)
}
