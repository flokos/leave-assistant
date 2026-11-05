import { Select, Table } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatRange } from '../../shared/dates'
import { formatDays } from '../../shared/days'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { useLeaveTypeName } from '../leave/api'
import { RequestDetail } from '../leave/RequestDetail'
import { type TeamRequestOut, useTeamRequests } from './api'

const STATUSES = ['PENDING_MANAGER', 'PENDING_HR', 'APPROVED', 'ACTIVE', 'COMPLETED', 'REJECTED', 'CANCELLED', 'MODIFICATION_PENDING']

export default function TeamRequestsPage() {
  const { t, i18n } = useTranslation()
  const [statuses, setStatuses] = useState<string[]>([])
  const [detail, setDetail] = useState<string | null>(null)
  const requests = useTeamRequests(statuses.length ? statuses : undefined)
  const typeName = useLeaveTypeName()
  return (
    <PageHeader
      title={t('team.requests')}
      extra={<Select mode="multiple" allowClear placeholder={t('leave.filterStatus')} style={{ minWidth: 240 }} value={statuses} onChange={setStatuses} options={STATUSES.map((s) => ({ value: s, label: t(`status.${s}`) }))} />}
    >
      {requests.error ? <ProblemAlert error={requests.error} /> : null}
      <Table<TeamRequestOut>
        rowKey={(row) => row.request.id}
        loading={requests.isPending}
        dataSource={requests.data?.items ?? []}
        onRow={(row) => ({ onClick: () => setDetail(row.request.id), style: { cursor: 'pointer' } })}
        data-testid="team-requests-table"
        columns={[
          { title: t('leave.requestNumber'), render: (_, row) => row.request.request_number },
          { title: t('team.employee'), dataIndex: 'employee_display_name' },
          { title: t('common.type'), render: (_, row) => typeName(row.request.leave_type) },
          { title: t('leave.dates'), render: (_, row) => formatRange(row.request.start_date, row.request.end_date, i18n.language) },
          { title: t('leave.workingDays'), render: (_, row) => formatDays(row.request.working_days) },
          { title: t('common.reason'), render: (_, row) => row.reason ?? '—' },
          { title: t('common.status'), render: (_, row) => <StatusTag status={row.request.status} /> },
        ]}
      />
      <RequestDetail id={detail} onClose={() => setDetail(null)} readOnly />
    </PageHeader>
  )
}
