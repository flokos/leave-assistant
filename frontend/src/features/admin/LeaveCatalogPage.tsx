import { Button, Card, DatePicker, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table } from 'antd'
import dayjs from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toIso } from '../../shared/dates'
import { formatDays } from '../../shared/days'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { type PolicyOut, useAdminLeaveTypes, useCreateLeaveType, useCreatePolicy, useDeletePolicy, usePolicies, useUpdateLeaveType, useUpdatePolicy } from './api'
import { handle } from '../../shared/async'

export default function LeaveCatalogPage() {
  const { t, i18n } = useTranslation()
  const types = useAdminLeaveTypes()
  const policies = usePolicies()
  const createType = useCreateLeaveType()
  const updateType = useUpdateLeaveType()
  const createPolicy = useCreatePolicy()
  const updatePolicy = useUpdatePolicy()
  const deletePolicy = useDeletePolicy()
  const [typeModal, setTypeModal] = useState<'new' | string | null>(null)
  const [policyModal, setPolicyModal] = useState<'new' | PolicyOut | null>(null)
  const [typeForm] = Form.useForm()
  const [policyForm] = Form.useForm()
  const typeName = (id: string) => {
    const lt = types.data?.find((x) => x.id === id)
    return lt ? (i18n.language === 'el' ? lt.name_el : lt.name_en) : id
  }
  const submitType = async () => {
    const v = await typeForm.validateFields()
    if (typeModal === 'new') await createType.mutateAsync(v)
    else if (typeModal) await updateType.mutateAsync({ id: typeModal, body: v })
    setTypeModal(null)
    typeForm.resetFields()
  }
  const submitPolicy = async () => {
    const v = await policyForm.validateFields()
    const body = { ...v, carryover_expires_on: v.carryover_expires_on ? toIso(v.carryover_expires_on) : null }
    if (policyModal === 'new') await createPolicy.mutateAsync(body)
    else if (policyModal) {
      const rest = { ...body }
      delete rest.leave_type
      delete rest.year
      await updatePolicy.mutateAsync({ id: policyModal.id, body: rest })
    }
    setPolicyModal(null)
    policyForm.resetFields()
  }
  return (
    <PageHeader title={t('nav.leaveCatalog')}>
      <Card title={t('admin.leaveTypes')} extra={<Button type="primary" onClick={() => setTypeModal('new')}>{t('admin.newLeaveType')}</Button>}>
        {types.error ? <ProblemAlert error={types.error} /> : null}
        <Table
          rowKey="id"
          size="small"
          loading={types.isPending}
          dataSource={types.data ?? []}
          pagination={false}
          columns={[
            { title: t('common.code'), dataIndex: 'code' },
            { title: t('admin.nameEl'), dataIndex: 'name_el' },
            { title: t('admin.nameEn'), dataIndex: 'name_en' },
            { title: t('admin.balanceBearing'), dataIndex: 'balance_bearing', render: (v: boolean) => (v ? t('common.yes') : t('common.no')) },
            { title: t('admin.retroactiveDays'), dataIndex: 'allow_retroactive_days' },
            { title: t('admin.documentAboveDays'), dataIndex: 'requires_document_above_days', render: (v: number | null) => v ?? '—' },
            { title: t('common.active'), dataIndex: 'active', render: (v: boolean) => (v ? t('common.yes') : t('common.no')) },
            { title: t('common.actions'), render: (_, lt) => <Button size="small" onClick={() => { typeForm.setFieldsValue(lt); setTypeModal(lt.id) }}>{t('common.edit')}</Button> },
          ]}
        />
      </Card>
      <Card title={t('admin.policies')} extra={<Button type="primary" onClick={() => setPolicyModal('new')}>{t('admin.newPolicy')}</Button>}>
        <Table<PolicyOut>
          rowKey="id"
          size="small"
          loading={policies.isPending}
          dataSource={policies.data ?? []}
          pagination={false}
          columns={[
            { title: t('common.type'), dataIndex: 'leave_type_id', render: (v: string) => typeName(v) },
            { title: t('common.year'), dataIndex: 'year' },
            { title: t('admin.entitlement'), dataIndex: 'entitlement_days', render: (v: number) => formatDays(v) },
            { title: t('admin.carryoverMax'), dataIndex: 'carryover_max_days', render: (v: number) => formatDays(v) },
            { title: t('admin.carryoverExpires'), dataIndex: 'carryover_expires_on', render: (v: string | null) => v ?? '—' },
            { title: t('admin.allowNegative'), dataIndex: 'allow_negative', render: (v: boolean) => (v ? t('common.yes') : t('common.no')) },
            { title: t('admin.minNotice'), dataIndex: 'min_notice_days' },
            { title: t('admin.maxConsecutive'), dataIndex: 'max_consecutive_days', render: (v: number | null) => v ?? '—' },
            { title: t('common.actions'), render: (_, p) => (
                <Space>
                  <Button size="small" onClick={() => { policyForm.setFieldsValue({ ...p, carryover_expires_on: p.carryover_expires_on ? dayjs(p.carryover_expires_on) : null }); setPolicyModal(p) }}>{t('common.edit')}</Button>
                  <Popconfirm title={t('common.delete') + '?'} onConfirm={() => deletePolicy.mutate(p.id)}>
                    <Button size="small" danger>{t('common.delete')}</Button>
                  </Popconfirm>
                </Space>
              ) },
          ]}
        />
      </Card>
      <Modal open={Boolean(typeModal)} title={typeModal === 'new' ? t('admin.newLeaveType') : t('admin.editLeaveType')} onCancel={() => setTypeModal(null)} onOk={handle(submitType)} confirmLoading={createType.isPending || updateType.isPending} destroyOnHidden>
        <Form form={typeForm} layout="vertical" initialValues={{ balance_bearing: true, allow_retroactive_days: 0, active: true }}>
          {typeModal === 'new' && <Form.Item name="code" label={t('common.code')} rules={[{ required: true }]}><Input /></Form.Item>}
          <Form.Item name="name_el" label={t('admin.nameEl')} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="name_en" label={t('admin.nameEn')} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="balance_bearing" label={t('admin.balanceBearing')} valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="allow_retroactive_days" label={t('admin.retroactiveDays')}><InputNumber min={0} /></Form.Item>
          <Form.Item name="requires_document_above_days" label={t('admin.documentAboveDays')}><InputNumber min={0} /></Form.Item>
          {typeModal !== 'new' && <Form.Item name="active" label={t('common.active')} valuePropName="checked"><Switch /></Form.Item>}
        </Form>
      </Modal>
      <Modal open={Boolean(policyModal)} title={policyModal === 'new' ? t('admin.newPolicy') : t('admin.editPolicy')} onCancel={() => setPolicyModal(null)} onOk={handle(submitPolicy)} confirmLoading={createPolicy.isPending || updatePolicy.isPending} destroyOnHidden>
        <Form form={policyForm} layout="vertical" initialValues={{ year: dayjs().year(), entitlement_days: 20, carryover_max_days: 5, allow_negative: false, min_notice_days: 0, prorate_on_hire: true }}>
          {policyModal === 'new' && (
            <>
              <Form.Item name="leave_type" label={t('common.type')} rules={[{ required: true }]}>
                <Select options={(types.data ?? []).map((lt) => ({ value: lt.code, label: i18n.language === 'el' ? lt.name_el : lt.name_en }))} />
              </Form.Item>
              <Form.Item name="year" label={t('common.year')} rules={[{ required: true }]}><InputNumber min={2000} max={2100} /></Form.Item>
            </>
          )}
          <Form.Item name="entitlement_days" label={t('admin.entitlement')} rules={[{ required: true }]}><InputNumber min={0} step={0.5} /></Form.Item>
          <Form.Item name="carryover_max_days" label={t('admin.carryoverMax')}><InputNumber min={0} step={0.5} /></Form.Item>
          <Form.Item name="carryover_expires_on" label={t('admin.carryoverExpires')}><DatePicker format="YYYY-MM-DD" /></Form.Item>
          <Form.Item name="allow_negative" label={t('admin.allowNegative')} valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="min_notice_days" label={t('admin.minNotice')}><InputNumber min={0} /></Form.Item>
          <Form.Item name="max_consecutive_days" label={t('admin.maxConsecutive')}><InputNumber min={1} /></Form.Item>
          <Form.Item name="prorate_on_hire" label={t('admin.prorate')} valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </PageHeader>
  )
}
