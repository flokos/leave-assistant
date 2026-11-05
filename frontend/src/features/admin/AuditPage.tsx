import { DatePicker, Input, Space, Table } from 'antd'
import type { Dayjs } from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDateTime } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { type AuditEntryOut, useAudit } from './api'

export function AuditTable({ entries, loading }: { entries: AuditEntryOut[]; loading: boolean }) {
  const { t, i18n } = useTranslation()
  return (
    <Table<AuditEntryOut>
      rowKey="id"
      loading={loading}
      dataSource={entries}
      locale={{ emptyText: t('admin.noEntries') }}
      data-testid="audit-table"
      columns={[
        { title: t('common.when'), dataIndex: 'occurred_at', render: (v: string) => formatDateTime(v, i18n.language) },
        { title: t('admin.action'), dataIndex: 'action' },
        { title: t('admin.actor'), render: (_, e) => `${e.actor_kind}:${e.actor_id.slice(0, 8)} [${e.actor_roles.join(', ')}]` },
        { title: t('admin.resource'), render: (_, e) => `${e.resource_type}${e.resource_id ? ':' + e.resource_id.slice(0, 8) : ''}` },
        { title: t('common.outcome'), dataIndex: 'outcome' },
        { title: t('common.details'), dataIndex: 'details', render: (v: Record<string, unknown>) => <code style={{ fontSize: 11 }}>{JSON.stringify(v)}</code> },
      ]}
    />
  )
}

export default function AuditPage() {
  const { t } = useTranslation()
  const [action, setAction] = useState('')
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const audit = useAudit({ action, from: range?.[0]?.toISOString(), to: range?.[1]?.toISOString() })
  return (
    <PageHeader
      title={t('admin.audit')}
      extra={
        <Space>
          <Input placeholder={t('admin.action')} value={action} onChange={(e) => setAction(e.target.value)} allowClear style={{ width: 240 }} />
          <DatePicker.RangePicker onChange={(v) => setRange(v as [Dayjs | null, Dayjs | null] | null)} showTime />
        </Space>
      }
    >
      {audit.error ? <ProblemAlert error={audit.error} /> : null}
      <AuditTable entries={audit.data?.items ?? []} loading={audit.isPending} />
    </PageHeader>
  )
}
