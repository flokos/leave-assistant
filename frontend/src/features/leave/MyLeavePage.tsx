import { Button, Card, Col, Modal, Row, Select, Space, Statistic, Table } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { currentYear, formatRange } from '../../shared/dates'
import { formatDays } from '../../shared/days'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { type LeaveRequestSummary, useBalances, useLeaveTypeName, useMyRequests } from './api'
import { NewRequestWizard } from './NewRequestWizard'
import { RequestDetail } from './RequestDetail'

const STATUSES = ['PENDING_MANAGER', 'PENDING_HR', 'APPROVED', 'ACTIVE', 'COMPLETED', 'REJECTED', 'CANCELLED', 'WITHDRAWN', 'MODIFICATION_PENDING']

export function BalancesCard({ year }: { year: number }) {
  const { t } = useTranslation()
  const balances = useBalances(year)
  const typeName = useLeaveTypeName()
  return (
    <Card title={t('leave.balances', { year })} loading={balances.isPending} data-testid="balances">
      {balances.error ? <ProblemAlert error={balances.error} /> : null}
      <Row gutter={16}>
        {(balances.data ?? []).map((b) => (
          <Col key={b.leave_type} xs={24} sm={12} md={8}>
            <Card size="small" data-testid={`balance-${b.leave_type}`}>
              <Statistic title={typeName(b.leave_type)} value={formatDays(b.available_days)} suffix={t('common.days').toLowerCase()} />
              <div style={{ opacity: 0.7 }}>
                {t('leave.balance')}: {formatDays(b.balance_days)} · {t('leave.pending')}: {formatDays(b.pending_days)}
              </div>
            </Card>
          </Col>
        ))}
      </Row>
    </Card>
  )
}

export default function MyLeavePage() {
  const { t, i18n } = useTranslation()
  const [statuses, setStatuses] = useState<string[]>([])
  const [wizardOpen, setWizardOpen] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const requests = useMyRequests(statuses.length ? statuses : undefined)
  const typeName = useLeaveTypeName()
  const year = currentYear()
  return (
    <PageHeader
      title={t('leave.title')}
      extra={
        <Button type="primary" onClick={() => setWizardOpen(true)} data-testid="new-request">
          {t('leave.newRequest')}
        </Button>
      }
    >
      <BalancesCard year={year} />
      <Card
        title={t('leave.history')}
        extra={
          <Select
            mode="multiple"
            allowClear
            placeholder={t('leave.filterStatus')}
            style={{ minWidth: 220 }}
            value={statuses}
            onChange={setStatuses}
            options={STATUSES.map((s) => ({ value: s, label: t(`status.${s}`) }))}
          />
        }
      >
        {requests.error ? <ProblemAlert error={requests.error} /> : null}
        <Table<LeaveRequestSummary>
          rowKey="id"
          loading={requests.isPending}
          dataSource={requests.data?.items ?? []}
          locale={{ emptyText: t('leave.noRequests') }}
          onRow={(row) => ({ onClick: () => setSelected(row.id), style: { cursor: 'pointer' } })}
          data-testid="requests-table"
          columns={[
            { title: t('leave.requestNumber'), dataIndex: 'request_number' },
            { title: t('common.type'), dataIndex: 'leave_type', render: (v: string | null) => typeName(v) },
            { title: t('leave.dates'), render: (_, row) => formatRange(row.start_date, row.end_date, i18n.language) },
            { title: t('leave.workingDays'), dataIndex: 'working_days', render: (v: number) => formatDays(v) },
            { title: t('common.status'), dataIndex: 'status', render: (v: string) => <StatusTag status={v} /> },
          ]}
        />
      </Card>
      <Modal open={wizardOpen} onCancel={() => setWizardOpen(false)} footer={null} title={t('leave.newRequest')} width={720} destroyOnHidden>
        <NewRequestWizard />
      </Modal>
      <RequestDetail id={selected} onClose={() => setSelected(null)} />
      <Space />
    </PageHeader>
  )
}
