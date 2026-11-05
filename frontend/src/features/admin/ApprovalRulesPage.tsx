import { Button, Card, DatePicker, Form, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tag } from 'antd'
import type { Dayjs } from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toIso } from '../../shared/dates'
import { formatDays } from '../../shared/days'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { ValidationReportView } from '../leave/ValidationReportView'
import { type RuleOut, useAdminLeaveTypes, useCreateRule, useDeleteRule, useEmployees, useRules, useSimulate, useUpdateRule } from './api'
import { handle } from '../../shared/async'

export default function ApprovalRulesPage() {
  const { t, i18n } = useTranslation()
  const rules = useRules()
  const types = useAdminLeaveTypes()
  const employees = useEmployees()
  const create = useCreateRule()
  const update = useUpdateRule()
  const remove = useDeleteRule()
  const simulate = useSimulate()
  const [editing, setEditing] = useState<RuleOut | 'new' | null>(null)
  const [form] = Form.useForm()
  const [simForm] = Form.useForm<{ leave_type: string; employee_id: string; dates: [Dayjs, Dayjs] }>()
  const typeLabel = (id: string | null) => {
    if (!id) return t('admin.anyType')
    const lt = types.data?.find((x) => x.id === id)
    return lt ? (i18n.language === 'el' ? lt.name_el : lt.name_en) : id
  }
  const submit = async () => {
    const v = await form.validateFields()
    const body = { ...v, leave_type: v.leave_type || null }
    if (editing === 'new') await create.mutateAsync(body)
    else if (editing) await update.mutateAsync({ id: editing.id, body })
    setEditing(null)
    form.resetFields()
  }
  const runSimulation = async () => {
    const v = await simForm.validateFields()
    await simulate.mutateAsync({ leave_type: v.leave_type, employee_id: v.employee_id, start_date: toIso(v.dates[0]), end_date: toIso(v.dates[1]) })
  }
  return (
    <PageHeader title={t('admin.rules')} extra={<Button type="primary" onClick={() => setEditing('new')}>{t('admin.newRule')}</Button>}>
      {rules.error ? <ProblemAlert error={rules.error} /> : null}
      <Table<RuleOut>
        rowKey="id"
        loading={rules.isPending}
        dataSource={rules.data ?? []}
        pagination={false}
        data-testid="rules-table"
        columns={[
          { title: t('common.priority'), dataIndex: 'priority' },
          { title: t('common.type'), dataIndex: 'leave_type_id', render: (v: string | null) => typeLabel(v) },
          { title: t('admin.minDays'), dataIndex: 'min_working_days', render: (v: number) => formatDays(v) },
          { title: t('admin.maxDays'), dataIndex: 'max_working_days', render: (v: number | null) => (v === null ? '∞' : formatDays(v)) },
          { title: t('common.steps'), dataIndex: 'steps', render: (steps: string[]) => (steps.length ? steps.map((s) => <Tag key={s}>{t(`stepRoles.${s}`)}</Tag>) : '—') },
          { title: t('admin.autoAcknowledge'), dataIndex: 'auto_acknowledge', render: (v: boolean) => (v ? t('common.yes') : t('common.no')) },
          { title: t('admin.requiresDocument'), dataIndex: 'requires_document', render: (v: boolean) => (v ? t('common.yes') : t('common.no')) },
          { title: t('common.active'), dataIndex: 'active', render: (v: boolean) => (v ? t('common.yes') : t('common.no')) },
          { title: t('common.actions'), render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => { form.setFieldsValue({ ...r, leave_type: types.data?.find((x) => x.id === r.leave_type_id)?.code ?? null }); setEditing(r) }}>{t('common.edit')}</Button>
                <Popconfirm title={t('admin.deleteRuleConfirm')} onConfirm={() => remove.mutate(r.id)}>
                  <Button size="small" danger>{t('common.delete')}</Button>
                </Popconfirm>
              </Space>
            ) },
        ]}
      />
      <Card title={t('admin.simulateTitle')}>
        <Form form={simForm} layout="inline" style={{ marginBottom: 16 }}>
          <Form.Item name="leave_type" rules={[{ required: true }]}>
            <Select placeholder={t('common.type')} style={{ minWidth: 180 }} options={(types.data ?? []).map((lt) => ({ value: lt.code, label: i18n.language === 'el' ? lt.name_el : lt.name_en }))} />
          </Form.Item>
          <Form.Item name="employee_id" rules={[{ required: true }]}>
            <Select placeholder={t('team.employee')} style={{ minWidth: 220 }} showSearch optionFilterProp="label" options={(employees.data?.items ?? []).map((e) => ({ value: e.id, label: `${e.first_name} ${e.last_name}` }))} />
          </Form.Item>
          <Form.Item name="dates" rules={[{ required: true }]}><DatePicker.RangePicker format="YYYY-MM-DD" /></Form.Item>
          <Button type="primary" onClick={handle(runSimulation)} loading={simulate.isPending}>{t('admin.simulate')}</Button>
        </Form>
        {simulate.error ? <ProblemAlert error={simulate.error} /> : null}
        {simulate.data && <ValidationReportView report={simulate.data} />}
      </Card>
      <Modal open={Boolean(editing)} title={editing === 'new' ? t('admin.newRule') : t('admin.editRule')} onCancel={() => setEditing(null)} onOk={handle(submit)} confirmLoading={create.isPending || update.isPending} destroyOnHidden>
        <Form form={form} layout="vertical" initialValues={{ steps: ['manager'], min_working_days: 0, priority: 100, requires_document: false, auto_acknowledge: false, active: true }}>
          <Form.Item name="leave_type" label={t('common.type')}>
            <Select allowClear placeholder={t('admin.anyType')} options={(types.data ?? []).map((lt) => ({ value: lt.code, label: i18n.language === 'el' ? lt.name_el : lt.name_en }))} />
          </Form.Item>
          <Form.Item name="min_working_days" label={t('admin.minDays')}><InputNumber min={0} step={0.5} /></Form.Item>
          <Form.Item name="max_working_days" label={t('admin.maxDays')}><InputNumber min={0} step={0.5} /></Form.Item>
          <Form.Item name="steps" label={t('common.steps')}>
            <Select mode="multiple" options={[{ value: 'manager', label: t('stepRoles.manager') }, { value: 'hr', label: t('stepRoles.hr') }]} />
          </Form.Item>
          <Form.Item name="priority" label={t('common.priority')} rules={[{ required: true }]}><InputNumber min={0} /></Form.Item>
          <Form.Item name="requires_document" label={t('admin.requiresDocument')} valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="auto_acknowledge" label={t('admin.autoAcknowledge')} valuePropName="checked"><Switch /></Form.Item>
          {editing !== 'new' && <Form.Item name="active" label={t('common.active')} valuePropName="checked"><Switch /></Form.Item>}
        </Form>
      </Modal>
    </PageHeader>
  )
}
