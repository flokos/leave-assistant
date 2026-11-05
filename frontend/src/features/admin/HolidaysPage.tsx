import { App, Button, DatePicker, Form, Input, InputNumber, Modal, Popconfirm, Space, Switch, Table } from 'antd'
import dayjs from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDate, toIso } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { type HolidayOut, useCreateHoliday, useDeleteHoliday, useHolidays, useImportHolidays } from './api'
import { handle } from '../../shared/async'

export default function HolidaysPage() {
  const { t, i18n } = useTranslation()
  const { message } = App.useApp()
  const [year, setYear] = useState(dayjs().year())
  const holidays = useHolidays(year)
  const create = useCreateHoliday()
  const remove = useDeleteHoliday()
  const importCsv = useImportHolidays()
  const [creating, setCreating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [form] = Form.useForm()
  const [importForm] = Form.useForm<{ csv: string }>()
  const submit = async () => {
    const v = await form.validateFields()
    await create.mutateAsync({ ...v, holiday_date: toIso(v.holiday_date) })
    setCreating(false)
    form.resetFields()
  }
  const submitImport = async () => {
    const v = await importForm.validateFields()
    const created = await importCsv.mutateAsync(v.csv)
    void message.success(t('admin.imported', { count: created.length }))
    setImporting(false)
    importForm.resetFields()
  }
  return (
    <PageHeader
      title={t('admin.holidays')}
      extra={
        <Space>
          <InputNumber value={year} onChange={(v) => v && setYear(v)} min={2000} max={2100} />
          <Button onClick={() => setImporting(true)} data-testid="import-holidays">{t('admin.importCsv')}</Button>
          <Button type="primary" onClick={() => setCreating(true)} data-testid="new-holiday">{t('admin.newHoliday')}</Button>
        </Space>
      }
    >
      {holidays.error ? <ProblemAlert error={holidays.error} /> : null}
      <Table<HolidayOut>
        rowKey="id"
        loading={holidays.isPending}
        dataSource={holidays.data ?? []}
        pagination={false}
        data-testid="holidays-table"
        columns={[
          { title: t('common.date'), dataIndex: 'holiday_date', render: (v: string) => formatDate(v, i18n.language) },
          { title: t('admin.nameEl'), dataIndex: 'name_el' },
          { title: t('admin.nameEn'), dataIndex: 'name_en' },
          { title: t('admin.recurring'), dataIndex: 'recurring', render: (v: boolean) => (v ? t('common.yes') : t('common.no')) },
          { title: t('common.actions'), render: (_, h) => (
              <Popconfirm title={t('admin.deleteHolidayConfirm')} onConfirm={() => remove.mutate(h.id)}>
                <Button size="small" danger data-testid={`delete-holiday-${h.holiday_date}`}>{t('common.delete')}</Button>
              </Popconfirm>
            ) },
        ]}
      />
      <Modal open={creating} title={t('admin.newHoliday')} onCancel={() => setCreating(false)} onOk={handle(submit)} confirmLoading={create.isPending} destroyOnHidden>
        {create.error ? <ProblemAlert error={create.error} /> : null}
        <Form form={form} layout="vertical" initialValues={{ recurring: false }}>
          <div data-testid="holiday-date">
            <Form.Item name="holiday_date" label={t('common.date')} rules={[{ required: true }]}><DatePicker format="YYYY-MM-DD" style={{ width: '100%' }} /></Form.Item>
          </div>
          <Form.Item name="name_el" label={t('admin.nameEl')} rules={[{ required: true }]}><Input data-testid="holiday-name-el" /></Form.Item>
          <Form.Item name="name_en" label={t('admin.nameEn')} rules={[{ required: true }]}><Input data-testid="holiday-name-en" /></Form.Item>
          <Form.Item name="recurring" label={t('admin.recurring')} valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
      <Modal open={importing} title={t('admin.importCsv')} onCancel={() => setImporting(false)} onOk={handle(submitImport)} confirmLoading={importCsv.isPending} destroyOnHidden>
        {importCsv.error ? <ProblemAlert error={importCsv.error} /> : null}
        <Form form={importForm} layout="vertical">
          <Form.Item name="csv" label="CSV" extra={t('admin.importHint')} rules={[{ required: true }]}>
            <Input.TextArea rows={8} placeholder={'date,name_el,name_en,recurring\n2026-12-24,Παραμονή Χριστουγέννων,Christmas Eve,true'} />
          </Form.Item>
        </Form>
      </Modal>
    </PageHeader>
  )
}
