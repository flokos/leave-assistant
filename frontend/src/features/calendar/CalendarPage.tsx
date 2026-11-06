import { CalendarOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons'
import { App, Button, Card, Col, Descriptions, Empty, Popconfirm, Row, Select, Space, Table, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router'
import { usePrincipal } from '../../auth/usePrincipal'
import { formatDateTime } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import {
  type ConnectionKind,
  type ConnectionOut,
  type ItemAction,
  type ReconciliationItemOut,
  type SyncJobOut,
  type SyncStatus,
  useConnections,
  useItemAction,
  useReconciliation,
  useRetrySyncJob,
  useRevokeConnection,
  useRunReconciliation,
  useStartConnection,
  useSyncJobs,
} from './api'

const STATUS_COLORS: Record<string, string> = {
  active: 'green',
  expired: 'orange',
  revoked: 'default',
  pending: 'gold',
  running: 'processing',
  succeeded: 'green',
  parked: 'orange',
  dead: 'red',
  open: 'gold',
  resolved: 'green',
  ignored: 'default',
}

function StatusTag({ value, testId }: { value: string; testId?: string }) {
  const { t } = useTranslation()
  return (
    <Tag color={STATUS_COLORS[value] ?? 'default'} data-testid={testId}>
      {t(`calendar.statuses.${value}`, { defaultValue: value })}
    </Tag>
  )
}

/** The `/calendar?status=…` return from the OAuth callback becomes a message, then the query string is cleared. */
function useCallbackResult() {
  const [params, setParams] = useSearchParams()
  const { message } = App.useApp()
  const { t } = useTranslation()
  useEffect(() => {
    const status = params.get('status')
    if (!status) return
    if (status === 'connected') void message.success(t('calendar.connected'))
    else void message.error(t(`calendar.errors.${params.get('reason') ?? 'unknown'}`, { defaultValue: t('calendar.errors.unknown') }))
    setParams({}, { replace: true })
  }, [params, setParams, message, t])
}

function ConnectionCard({ kind, connection, provider }: { kind: ConnectionKind; connection?: ConnectionOut; provider: string }) {
  const { t, i18n } = useTranslation()
  const start = useStartConnection()
  const revoke = useRevokeConnection()
  const connected = connection && connection.status !== 'revoked'
  return (
    <Card
      title={
        <Space>
          <CalendarOutlined />
          {t(`calendar.kinds.${kind}`)}
        </Space>
      }
      extra={connected ? <StatusTag value={connection.status} testId={`connection-status-${kind}`} /> : <Tag data-testid={`connection-status-${kind}`}>{t('calendar.notConnected')}</Tag>}
      data-testid={`connection-${kind}`}
    >
      <Typography.Paragraph type="secondary">{t(`calendar.kindHelp.${kind}`)}</Typography.Paragraph>
      {connected ? (
        <Descriptions size="small" column={1}>
          <Descriptions.Item label={t('calendar.provider')}>{t(`calendar.providers.${connection.provider}`, { defaultValue: connection.provider })}</Descriptions.Item>
          <Descriptions.Item label={t('calendar.lastSuccess')}>{formatDateTime(connection.last_success_at, i18n.language)}</Descriptions.Item>
          {connection.last_error ? <Descriptions.Item label={t('calendar.lastError')}>{connection.last_error}</Descriptions.Item> : null}
        </Descriptions>
      ) : null}
      <Space style={{ marginTop: 12 }}>
        {!connected || connection.status === 'expired' ? (
          <Button type="primary" loading={start.isPending} onClick={() => start.mutate(kind)} data-testid={`connect-${kind}`}>
            {connected ? t('calendar.reauthorise') : t('calendar.connect', { provider: t(`calendar.providers.${provider}`, { defaultValue: provider }) })}
          </Button>
        ) : null}
        {connected ? (
          <Popconfirm title={t('calendar.disconnectConfirm')} onConfirm={() => revoke.mutate(connection.id)} okButtonProps={{ danger: true }}>
            <Button danger loading={revoke.isPending} data-testid={`disconnect-${kind}`}>
              {t('calendar.disconnect')}
            </Button>
          </Popconfirm>
        ) : null}
      </Space>
    </Card>
  )
}

function SyncJobs({ canRetry }: { canRetry: boolean }) {
  const { t, i18n } = useTranslation()
  const [status, setStatus] = useState<SyncStatus | undefined>(undefined)
  const jobs = useSyncJobs(status)
  const retry = useRetrySyncJob()
  return (
    <Card
      title={t('calendar.syncHistory')}
      extra={
        <Select
          allowClear
          placeholder={t('common.status')}
          value={status}
          onChange={setStatus}
          style={{ minWidth: 160 }}
          options={(['pending', 'running', 'succeeded', 'parked', 'dead'] as SyncStatus[]).map((s) => ({ value: s, label: t(`calendar.statuses.${s}`) }))}
        />
      }
      data-testid="sync-jobs"
    >
      {jobs.error ? <ProblemAlert error={jobs.error} /> : null}
      <Table<SyncJobOut>
        rowKey="id"
        size="small"
        loading={jobs.isPending}
        dataSource={jobs.data?.items ?? []}
        locale={{ emptyText: t('calendar.noJobs') }}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: t('calendar.request'), dataIndex: 'request_number' },
          { title: t('calendar.calendar'), dataIndex: 'connection_kind', render: (v: string | null) => (v ? t(`calendar.kinds.${v}`) : '—') },
          { title: t('calendar.operation'), dataIndex: 'operation', render: (v: string) => t(`calendar.operations.${v}`) },
          { title: t('common.status'), dataIndex: 'status', render: (v: string, row) => <StatusTag value={v} testId={`job-status-${row.request_number}-${row.operation}`} /> },
          { title: t('calendar.attempts'), dataIndex: 'attempts' },
          { title: t('calendar.nextAttempt'), dataIndex: 'next_attempt_at', render: (v: string | null) => formatDateTime(v, i18n.language) },
          { title: t('calendar.errorClass'), dataIndex: 'error_class', render: (v: string | null) => (v ? t(`calendar.errorClasses.${v}`) : '—') },
          { title: t('common.when'), dataIndex: 'updated_at', render: (v: string) => formatDateTime(v, i18n.language) },
          ...(canRetry
            ? [
                {
                  title: t('common.actions'),
                  render: (_: unknown, row: SyncJobOut) =>
                    row.status === 'dead' ? (
                      <Button size="small" icon={<SyncOutlined />} loading={retry.isPending} onClick={() => retry.mutate(row.id)} data-testid={`retry-${row.id}`}>
                        {t('calendar.retry')}
                      </Button>
                    ) : (
                      '—'
                    ),
                },
              ]
            : []),
        ]}
      />
    </Card>
  )
}

function Reconciliation() {
  const { t, i18n } = useTranslation()
  const report = useReconciliation(true)
  const run = useRunReconciliation()
  const act = useItemAction()
  const actions = (item: ReconciliationItemOut): ItemAction[] => {
    if (item.status !== 'open') return []
    if (item.finding === 'unknown_external_id') return ['relink', 'ignore']
    if (item.finding === 'orphaned') return ['delete-orphan', 'ignore']
    return ['ignore']
  }
  const data = report.data
  return (
    <Card
      title={t('calendar.reconciliation')}
      extra={
        <Button icon={<ReloadOutlined />} loading={run.isPending} onClick={() => run.mutate()} data-testid="reconcile">
          {t('calendar.runReconciliation')}
        </Button>
      }
      data-testid="reconciliation"
    >
      {report.error ? <ProblemAlert error={report.error} /> : null}
      {data?.run_at ? (
        <Typography.Paragraph type="secondary">{t('calendar.lastRun', { when: formatDateTime(data.run_at, i18n.language) })}</Typography.Paragraph>
      ) : null}
      {data && Object.keys(data.errors).length ? <Typography.Paragraph type="danger">{t('calendar.reconcileErrors')}</Typography.Paragraph> : null}
      {data && !data.items.length ? (
        <Empty description={t('calendar.inSync')} data-testid="reconciliation-clean" />
      ) : (
        <Table<ReconciliationItemOut>
          rowKey="id"
          size="small"
          loading={report.isPending}
          dataSource={data?.items ?? []}
          pagination={false}
          columns={[
            { title: t('calendar.finding'), dataIndex: 'finding', render: (v: string) => t(`calendar.findings.${v}`) },
            { title: t('common.status'), dataIndex: 'status', render: (v: string) => <StatusTag value={v} /> },
            { title: t('calendar.result'), dataIndex: 'action', render: (v: string | null) => (v ? t(`calendar.itemActions.${v}`, { defaultValue: v }) : '—') },
            {
              title: t('common.actions'),
              render: (_, item) => (
                <Space>
                  {actions(item).map((action) => (
                    <Button key={action} size="small" danger={action === 'delete-orphan'} onClick={() => act.mutate({ id: item.id, action })} data-testid={`item-${action}-${item.id}`}>
                      {t(`calendar.actions.${action}`)}
                    </Button>
                  ))}
                </Space>
              ),
            },
          ]}
        />
      )}
    </Card>
  )
}

/** Calendar integration (doc 10 §7): connections, sync history and — for company administrators — reconciliation. */
export default function CalendarPage() {
  const { t } = useTranslation()
  const principal = usePrincipal()
  const connections = useConnections()
  useCallbackResult()
  const isCompanyAdmin = principal.hasRole('company_admin')
  const items = connections.data?.items ?? []
  const provider = connections.data?.provider ?? 'mock'
  return (
    <PageHeader title={t('calendar.title')}>
      {connections.error ? <ProblemAlert error={connections.error} /> : null}
      <Typography.Paragraph type="secondary">{t('calendar.privacy')}</Typography.Paragraph>
      <Row gutter={[16, 16]}>
        {isCompanyAdmin ? (
          <Col xs={24} md={12}>
            <ConnectionCard kind="company_shared" connection={items.find((c) => c.kind === 'company_shared')} provider={provider} />
          </Col>
        ) : null}
        <Col xs={24} md={12}>
          <ConnectionCard kind="personal" connection={items.find((c) => c.kind === 'personal')} provider={provider} />
        </Col>
      </Row>
      <SyncJobs canRetry={isCompanyAdmin} />
      {isCompanyAdmin ? <Reconciliation /> : null}
    </PageHeader>
  )
}
