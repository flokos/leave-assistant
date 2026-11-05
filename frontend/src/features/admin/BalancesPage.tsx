import { App, Button, Card, DatePicker, Form, Input, InputNumber, Select, Space, Table } from 'antd'
import dayjs from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { usePrincipal } from '../../auth/usePrincipal'
import { toIso } from '../../shared/dates'
import { formatDays } from '../../shared/days'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { useLeaveTypes } from '../leave/api'
import { type TenantBalanceOut, usePostAdjustment, useTeamBalanceNames, useTenantBalances } from './balances'
import { handle } from '../../shared/async'

export default function BalancesPage() {
  const { t, i18n } = useTranslation()
  const { message } = App.useApp()
  const principal = usePrincipal()
  const [year, setYear] = useState(dayjs().year())
  const balances = useTenantBalances(year)
  const names = useTeamBalanceNames()
  const types = useLeaveTypes()
  const adjust = usePostAdjustment()
  const [form] = Form.useForm()
  const typeName = (id: string) => {
    const lt = types.data?.find((x) => x.id === id)
    return lt ? (i18n.language === 'el' ? lt.name_el : lt.name_en) : id
  }
  const submit = async () => {
    const v = await form.validateFields()
    await adjust.mutateAsync({ ...v, quantity_days: String(v.quantity_days), effective_date: v.effective_date ? toIso(v.effective_date) : null })
    void message.success(t('admin.adjustmentPosted'))
    form.resetFields()
  }
  return (
    <PageHeader title={t('admin.balances')} extra={<InputNumber value={year} onChange={(v) => v && setYear(v)} min={2000} max={2100} />}>
      {balances.error ? <ProblemAlert error={balances.error} /> : null}
      <Table<TenantBalanceOut>
        rowKey={(r) => `${r.employee_id}-${r.leave_type_id}`}
        loading={balances.isPending}
        dataSource={balances.data ?? []}
        data-testid="balances-table"
        columns={[
          { title: t('team.employee'), dataIndex: 'employee_id', render: (v: string) => names(v) },
          { title: t('common.type'), dataIndex: 'leave_type_id', render: (v: string) => typeName(v) },
          { title: t('leave.balance'), dataIndex: 'balance_days', render: (v: number) => formatDays(v) },
          { title: t('leave.pending'), dataIndex: 'pending_days', render: (v: number) => formatDays(v) },
          { title: t('leave.available'), dataIndex: 'available_days', render: (v: number) => formatDays(v) },
        ]}
      />
      {principal.hasRole('hr_admin') && (
        <Card title={t('admin.adjustment')}>
          {adjust.error ? <ProblemAlert error={adjust.error} /> : null}
          <Form form={form} layout="vertical" initialValues={{ quantity_days: 1 }}>
            <Space wrap align="start">
              <Form.Item name="employee_id" label={t('team.employee')} rules={[{ required: true }]}>
                <Select showSearch optionFilterProp="label" style={{ minWidth: 240 }} options={names.options} />
              </Form.Item>
              <Form.Item name="leave_type" label={t('common.type')} rules={[{ required: true }]}>
                <Select style={{ minWidth: 180 }} options={(types.data ?? []).filter((lt) => lt.balance_bearing).map((lt) => ({ value: lt.code, label: i18n.language === 'el' ? lt.name_el : lt.name_en }))} />
              </Form.Item>
              <Form.Item name="quantity_days" label={t('admin.quantity')} rules={[{ required: true }]}><InputNumber step={0.5} /></Form.Item>
              <Form.Item name="period_year" label={t('admin.periodYear')}><InputNumber min={2000} max={2100} /></Form.Item>
              <Form.Item name="effective_date" label={t('admin.effectiveDate')}><DatePicker format="YYYY-MM-DD" /></Form.Item>
            </Space>
            <Form.Item name="reason" label={t('common.reason')} rules={[{ required: true }]}><Input maxLength={500} /></Form.Item>
            <Button type="primary" onClick={handle(submit)} loading={adjust.isPending}>{t('admin.postAdjustment')}</Button>
          </Form>
        </Card>
      )}
    </PageHeader>
  )
}
