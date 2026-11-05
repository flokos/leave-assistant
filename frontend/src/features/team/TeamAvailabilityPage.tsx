import { Card, DatePicker, Space, Table } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatRange, toIso } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { useLeaveTypeName } from '../leave/api'
import { type AbsenceOut, useAvailability, useTeamEmployees } from './api'

export default function TeamAvailabilityPage() {
  const { t, i18n } = useTranslation()
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().startOf('month'), dayjs().add(2, 'month').endOf('month')])
  const availability = useAvailability(toIso(range[0]), toIso(range[1]))
  const employees = useTeamEmployees()
  const typeName = useLeaveTypeName()
  return (
    <PageHeader
      title={t('team.availability')}
      extra={<DatePicker.RangePicker value={range} onChange={(v) => v && v[0] && v[1] && setRange([v[0], v[1]])} format="YYYY-MM-DD" />}
    >
      {availability.error ? <ProblemAlert error={availability.error} /> : null}
      <Table<AbsenceOut>
        rowKey={(row) => `${row.employee_id}-${row.start_date}`}
        loading={availability.isPending}
        dataSource={availability.data ?? []}
        locale={{ emptyText: t('team.noAbsences') }}
        data-testid="availability-table"
        columns={[
          { title: t('team.employee'), dataIndex: 'display_name' },
          { title: t('team.range'), render: (_, row) => formatRange(row.start_date, row.end_date, i18n.language) },
          { title: t('common.type'), render: (_, row) => typeName(row.leave_type) },
          { title: t('common.status'), dataIndex: 'status', render: (v: string) => <StatusTag status={v} /> },
        ]}
      />
      <Card title={t('team.employees')} size="small" loading={employees.isPending}>
        <Space wrap>{(employees.data ?? []).map((e) => <span key={e.id}>{e.first_name} {e.last_name} ({e.employee_number})</span>)}</Space>
      </Card>
    </PageHeader>
  )
}
