import { DeleteOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, Input, List, Popconfirm, Space, Tag, Typography } from 'antd'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { describeError } from '../../shared/errors'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { type CitationItem, type MessageOut, type ProposalCardOut, useArchiveConversation, useConversations, useCreateConversation, useMessages } from './api'
import { CitationDrawer } from './CitationDrawer'
import { ProposalCard } from './ProposalCard'
import { type ChatStreamEvent, type ResumeBody, streamMessage, streamResume } from './sse'

interface ToolChip {
  name: string
  status: string
}

interface Pending {
  user: string | null // null for a decision on a card (resume)
  text: string
  stage: string | null
  citations: CitationItem[]
  tools: ToolChip[]
  card: ProposalCardOut | null
  outcome: string | null
  error: string | null
}

const OUTCOME_COLORS: Record<string, string> = {
  insufficient_evidence: 'gold',
  blocked: 'red',
  clarify: 'blue',
  scope: 'default',
  executed: 'green',
  failed: 'red',
  rejected: 'default',
  fallback: 'orange', // M10 (R-36): the verifier rejected every draft; the text is the deterministic fallback
}
const TOOL_COLORS: Record<string, string> = { started: 'processing', ok: 'success', denied: 'error', not_found: 'warning', invalid: 'warning', error: 'error' }

function ToolChips({ tools }: { tools: ToolChip[] }) {
  const { t } = useTranslation()
  if (!tools.length) return null
  return (
    <Space wrap size={4} style={{ marginBottom: 4 }} data-testid="tool-chips">
      {tools.map((tool, i) => (
        <Tag key={`${tool.name}-${i}`} color={TOOL_COLORS[tool.status] ?? 'default'}>
          {t(`chat.tools.${tool.name}`, { defaultValue: tool.name })}
        </Tag>
      ))}
    </Space>
  )
}

/** Answer text with [n] markers turned into buttons that open the cited chunk. */
function AnswerText({ text, citations, onCite }: { text: string; citations: CitationItem[]; onCite: (chunkId: string) => void }) {
  const parts: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(/\[(\d{1,2})\]/g)) {
    const n = Number(match[1])
    const citation = citations.find((c) => c.n === n)
    parts.push(text.slice(last, match.index))
    parts.push(
      citation ? (
        <a key={`${match.index}-${n}`} onClick={() => onCite(citation.chunk_id)} data-testid={`cite-${n}`}>
          <sup>[{n}]</sup>
        </a>
      ) : (
        match[0]
      ),
    )
    last = (match.index ?? 0) + match[0].length
  }
  parts.push(text.slice(last))
  return <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 4 }}>{parts}</Typography.Paragraph>
}

function Sources({ citations, onCite }: { citations: CitationItem[]; onCite: (chunkId: string) => void }) {
  const { t } = useTranslation()
  if (!citations.length) return null
  return (
    <Space wrap size={4} data-testid="sources">
      <Typography.Text type="secondary">{t('chat.sources')}:</Typography.Text>
      {citations.map((c) => (
        <Tag key={c.n} style={{ cursor: 'pointer' }} onClick={() => onCite(c.chunk_id)}>
          [{c.n}] {c.title}
          {c.heading_path.length > 1 ? ` › ${c.heading_path[c.heading_path.length - 1]}` : ''}
          {c.page ? ` · ${t('documents.page', { page: c.page })}` : ''}
        </Tag>
      ))}
    </Space>
  )
}

function Bubble({ role, children }: { role: string; children: ReactNode }) {
  const mine = role === 'user'
  return (
    <div style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start', margin: '8px 0' }} data-testid={`message-${role}`}>
      <div style={{ maxWidth: '78%', padding: '8px 12px', borderRadius: 10, background: mine ? '#e6f4ff' : '#fafafa', border: '1px solid #f0f0f0' }}>{children}</div>
    </div>
  )
}

export default function ChatPage() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const conversations = useConversations()
  const create = useCreateConversation()
  const archive = useArchiveConversation()
  const [selected, setSelected] = useState<string | null>(null)
  // "New" must stay new: without this the effect below re-selects the latest conversation (M6 bug, worklog M7),
  // and the next message — possibly from another tab — would land in it and supersede its open card
  const [fresh, setFresh] = useState(false)
  const messages = useMessages(selected)
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<Pending | null>(null)
  const [citation, setCitation] = useState<string | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const busy = pending !== null && pending.outcome === null && pending.error === null

  useEffect(() => {
    if (!selected && !fresh && conversations.data?.items.length) setSelected(conversations.data.items[0].id)
  }, [conversations.data, selected, fresh])
  useEffect(() => {
    // a block body on purpose: newer browsers return a Promise from scrollIntoView, which React would call as cleanup
    void bottom.current?.scrollIntoView?.({ block: 'end' })
  }, [messages.data, pending])

  const update = (change: (current: Pending) => Partial<Pending>) =>
    setPending((current) => (current ? { ...current, ...change(current) } : current))

  const onEvent = (event: ChatStreamEvent) => {
    if (event.event === 'status') update(() => ({ stage: String(event.data.stage) }))
    else if (event.event === 'tool_call') {
      const chip = { name: String(event.data.name), status: String(event.data.status) }
      update((current) => {
        const tools = [...current.tools]
        const open = tools.findIndex((x) => x.name === chip.name && x.status === 'started')
        if (chip.status !== 'started' && open >= 0) tools[open] = chip
        else tools.push(chip)
        return { tools }
      })
    } else if (event.event === 'token') update((current) => ({ text: current.text + String(event.data.text) }))
    else if (event.event === 'citations') update(() => ({ citations: event.data.items as CitationItem[] }))
    else if (event.event === 'interrupt') update(() => ({ card: event.data.card as ProposalCardOut, outcome: 'interrupted' }))
    else if (event.event === 'done') update(() => ({ outcome: String(event.data.outcome) }))
    else if (event.event === 'error')
      update(() => ({ error: t(`problems.${String(event.data.type)}`, { defaultValue: String(event.data.title) }) }))
  }

  const run = async (start: Pending, stream: () => Promise<void>) => {
    setPending(start)
    try {
      await stream()
    } catch (error) {
      update(() => ({ error: describeError(error, t) }))
      return
    }
    await client.invalidateQueries({ queryKey: ['chat'] })
    setPending((current) => (current?.error ? current : null))
  }

  const blank: Omit<Pending, 'user'> = { text: '', stage: null, citations: [], tools: [], card: null, outcome: null, error: null }

  const send = async () => {
    const text = draft.trim()
    if (!text || busy) return
    let conversationId = selected
    if (!conversationId) {
      conversationId = (await create.mutateAsync()).id
      setSelected(conversationId)
      setFresh(false)
    }
    setDraft('')
    const target = conversationId
    await run({ ...blank, user: text }, () => streamMessage(target, text, onEvent))
  }

  const decide = async (card: ProposalCardOut, decision: ResumeBody['decision'], edits?: ResumeBody['edits']) => {
    if (!selected || busy) return
    const target = selected
    const body: ResumeBody = { proposal_id: card.proposal_id, decision, payload_hash: card.payload_hash, ...(edits ? { edits } : {}) }
    await run({ ...blank, user: null, stage: 'executing' }, () => streamResume(target, body, onEvent))
  }

  const history: MessageOut[] = messages.data ?? []
  const lastPending = [...history].reverse().find((m) => m.card?.status === 'pending')?.card?.proposal_id
  return (
    <PageHeader title={t('chat.title')}>
      <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', gap: 16, alignItems: 'start' }}>
        <Card
          size="small"
          title={t('chat.conversations')}
          extra={<Button size="small" icon={<PlusOutlined />} onClick={() => { setSelected(null); setFresh(true); setPending(null) }} data-testid="new-conversation">{t('chat.new')}</Button>}
        >
          {conversations.error ? <ProblemAlert error={conversations.error} /> : null}
          <List
            size="small"
            loading={conversations.isPending}
            dataSource={conversations.data?.items ?? []}
            locale={{ emptyText: t('chat.noConversations') }}
            renderItem={(c) => (
              <List.Item
                onClick={() => { setSelected(c.id); setFresh(false); setPending(null) }}
                style={{ cursor: 'pointer', background: c.id === selected ? '#f0f5ff' : undefined, paddingInline: 8 }}
                actions={[
                  <Popconfirm key="del" title={t('chat.deleteConfirm')} onConfirm={() => { archive.mutate(c.id); if (c.id === selected) setSelected(null) }}>
                    <DeleteOutlined onClick={(e) => e.stopPropagation()} />
                  </Popconfirm>,
                ]}
                data-testid="conversation-item"
              >
                <Typography.Text ellipsis style={{ maxWidth: 170 }}>{c.title || t('chat.untitled')}</Typography.Text>
              </List.Item>
            )}
          />
        </Card>
        <Card size="small" data-testid="chat-pane">
          <div style={{ minHeight: 360, maxHeight: '62vh', overflowY: 'auto', paddingRight: 4 }}>
            {messages.error ? <ProblemAlert error={messages.error} /> : null}
            {!history.length && !pending && <Empty description={t('chat.intro')} image={Empty.PRESENTED_IMAGE_SIMPLE} />}
            {history.map((m) => (
              <Bubble key={m.id} role={m.role}>
                {m.role === 'assistant' ? <AnswerText text={m.text} citations={m.citations} onCite={setCitation} /> : <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>{m.text}</Typography.Text>}
                {m.role === 'assistant' && <Sources citations={m.citations} onCite={setCitation} />}
                {m.card && (
                  <ProposalCard card={m.card} active={m.card.proposal_id === lastPending && !pending} busy={busy}
                    onDecide={(decision, edits) => { if (m.card) void decide(m.card, decision, edits) }} />
                )}
                {m.outcome && OUTCOME_COLORS[m.outcome] && <Tag color={OUTCOME_COLORS[m.outcome]} data-testid={`outcome-${m.outcome}`}>{t(`chat.outcomes.${m.outcome}`)}</Tag>}
              </Bubble>
            ))}
            {pending && (
              <>
                {pending.user !== null && <Bubble role="user"><Typography.Text style={{ whiteSpace: 'pre-wrap' }}>{pending.user}</Typography.Text></Bubble>}
                <Bubble role="assistant">
                  <ToolChips tools={pending.tools} />
                  {pending.text ? <AnswerText text={pending.text} citations={pending.citations} onCite={setCitation} /> : null}
                  {pending.card && <ProposalCard card={pending.card} active={false} busy={busy} onDecide={() => undefined} />}
                  <Sources citations={pending.citations} onCite={setCitation} />
                  {busy && <Typography.Text type="secondary" data-testid="chat-status">{t(`chat.stages.${pending.stage ?? 'thinking'}`, { defaultValue: t('chat.stages.thinking') })}…</Typography.Text>}
                  {pending.error && <Alert type="error" showIcon message={pending.error} data-testid="chat-error" />}
                </Bubble>
              </>
            )}
            <div ref={bottom} />
          </div>
          <Space.Compact style={{ width: '100%', marginTop: 12 }}>
            <Input.TextArea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); void send() } }}
              autoSize={{ minRows: 1, maxRows: 5 }}
              maxLength={2000}
              placeholder={t('chat.placeholder')}
              disabled={busy}
              data-testid="chat-input"
            />
            <Button type="primary" icon={<SendOutlined />} onClick={() => void send()} loading={busy} disabled={!draft.trim()} data-testid="chat-send">
              {t('chat.send')}
            </Button>
          </Space.Compact>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t('chat.disclaimer')}</Typography.Text>
        </Card>
      </div>
      <CitationDrawer chunkId={citation} onClose={() => setCitation(null)} />
    </PageHeader>
  )
}
