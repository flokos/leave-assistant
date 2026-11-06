import { CheckOutlined, CloseOutlined, EditOutlined } from '@ant-design/icons'
import { Alert, Button, Card, DatePicker, Descriptions, Form, Select, Space, Tag, Typography } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { type ReactNode, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLeaveTypes } from '../leave/api'
import type { ProposalCardOut } from './api'
import type { ResumeBody } from './sse'

const STATUS_COLORS: Record<string, string> = { pending: 'processing', confirmed: 'processing', executed: 'success', rejected: 'default', expired: 'warning', failed: 'error' }

type Summary = Record<string, unknown>

function text(value: unknown): string {
  return value === null || value === undefined ? '—' : String(value)
}

/** A confirmation card (doc 09 §4 ProposalCard): facts from the stored proposal, never model text. */
export function ProposalCard({
  card,
  active,
  busy,
  onDecide,
}: {
  card: ProposalCardOut
  active: boolean
  busy: boolean
  onDecide: (decision: ResumeBody['decision'], edits?: ResumeBody['edits']) => void
}) {
  const { t, i18n } = useTranslation()
  const types = useLeaveTypes()
  const [editing, setEditing] = useState(false)
  const s = card.summary as Summary
  const el = i18n.language === 'el'
  const typeName = (code: unknown): string => {
    const names = s.leave_type_name as { el?: string; en?: string } | undefined
    if (names && code === s.leave_type) return (el ? names.el : names.en) ?? text(code)
    const found = types.data?.find((x) => x.code === code)
    return found ? (el ? found.name_el : found.name_en) : text(code)
  }
  const period = (start: unknown, end: unknown) => (start === end ? text(start) : `${text(start)} – ${text(end)}`)
  const chain = (roles: unknown) => (Array.isArray(roles) && roles.length ? roles.map((r) => t(`chat.card.roles.${String(r)}`)).join(' → ') : t('chat.card.autoAcknowledged'))

  const rows: [string, ReactNode][] = []
  if (card.kind === 'submit') {
    rows.push([t('chat.card.leaveType'), typeName(s.leave_type)], [t('chat.card.period'), period(s.start_date, s.end_date)], [t('chat.card.workingDays'), text(s.working_days)])
    if (s.available_before !== null && s.available_before !== undefined)
      rows.push([t('chat.card.balance'), `${text(s.available_before)} → ${text(s.available_after)}`])
    rows.push([t('chat.card.approvalChain'), chain(s.approval_chain)])
    const holidays = (s.excluded as { date: string; reason: string; name?: string }[] | undefined)?.filter((e) => e.reason === 'holiday') ?? []
    if (holidays.length) rows.push([t('chat.card.holidays'), holidays.map((h) => `${h.date}${h.name ? ` (${h.name})` : ''}`).join(', ')])
  } else if (card.kind === 'modify') {
    const from = (s.from ?? {}) as Summary
    rows.push([t('chat.card.request'), text(s.request_number)], [t('chat.card.from'), period(from.start_date, from.end_date)], [t('chat.card.to'), period(s.start_date, s.end_date)], [t('chat.card.workingDays'), text(s.working_days)], [t('chat.card.approvalChain'), chain(s.approval_chain)])
  } else if (card.kind === 'cancel') {
    rows.push([t('chat.card.request'), text(s.request_number)], [t('chat.card.leaveType'), typeName(s.leave_type)], [t('chat.card.period'), period(s.start_date, s.end_date)], [t('chat.card.workingDays'), text(s.working_days)])
  } else {
    rows.push([t('chat.card.request'), text(s.request_number)], [t('chat.card.requester'), text(s.requester)], [t('chat.card.leaveType'), typeName(s.leave_type)], [t('chat.card.period'), period(s.start_date, s.end_date)], [t('chat.card.workingDays'), text(s.working_days)], [t('chat.card.step'), t(`chat.card.roles.${text(s.step_role)}`)])
    if (s.comment) rows.push([t('chat.card.comment'), text(s.comment)])
  }
  const pending = card.status === 'pending'
  const outcome = card.outcome as Summary
  return (
    <Card size="small" style={{ marginTop: 8, borderColor: pending ? '#91caff' : undefined }} data-testid="proposal-card"
      title={<Space>{t(`chat.card.kinds.${card.kind}`)}<Tag color={STATUS_COLORS[card.status]} data-testid="card-status">{t(`chat.card.status.${card.status}`)}</Tag></Space>}>
      <Descriptions size="small" column={1} items={rows.map(([label, value], i) => ({ key: String(i), label, children: value }))} />
      {pending && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{t('chat.card.expires', { time: dayjs(card.expires_at).format('HH:mm') })}</Typography.Text>}
      {card.status === 'executed' && outcome.request_number ? <Typography.Paragraph style={{ marginBottom: 0 }}>{t('chat.card.done', { number: text(outcome.request_number) })}</Typography.Paragraph> : null}
      {card.status === 'failed' && <Alert type="error" showIcon message={text(outcome.detail ?? outcome.code)} />}
      {active && pending && !editing && (
        <Space style={{ marginTop: 8 }}>
          <Button type="primary" icon={<CheckOutlined />} loading={busy} onClick={() => onDecide('confirm')} data-testid="card-confirm">{t('chat.card.confirm')}</Button>
          <Button icon={<CloseOutlined />} disabled={busy} onClick={() => onDecide('reject')} data-testid="card-reject">{t('chat.card.reject')}</Button>
          {card.actions.includes('edit') && <Button icon={<EditOutlined />} disabled={busy} onClick={() => setEditing(true)} data-testid="card-edit">{t('chat.card.edit')}</Button>}
        </Space>
      )}
      {active && pending && editing && (
        <Form layout="vertical" style={{ marginTop: 8 }} initialValues={{ period: [dayjs(text(s.start_date)), dayjs(text(s.end_date))], leave_type: s.leave_type }}
          onFinish={(values: { period: [Dayjs, Dayjs]; leave_type?: string }) => {
            setEditing(false)
            onDecide('edit', { start_date: values.period[0].format('YYYY-MM-DD'), end_date: values.period[1].format('YYYY-MM-DD'), leave_type: card.kind === 'submit' ? values.leave_type : undefined })
          }}>
          <div data-testid="card-edit-period"><Form.Item name="period" label={t('chat.card.period')} rules={[{ required: true }]}><DatePicker.RangePicker format="YYYY-MM-DD" /></Form.Item></div>
          {card.kind === 'submit' && (
            <Form.Item name="leave_type" label={t('chat.card.leaveType')}>
              <Select style={{ width: 220 }} options={(types.data ?? []).map((lt) => ({ value: lt.code, label: el ? lt.name_el : lt.name_en }))} />
            </Form.Item>
          )}
          <Space><Button type="primary" htmlType="submit" data-testid="card-edit-save">{t('chat.card.prepareAgain')}</Button><Button onClick={() => setEditing(false)}>{t('common.cancel')}</Button></Space>
        </Form>
      )}
    </Card>
  )
}
