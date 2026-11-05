import { Button, Form, Input, Modal, Popconfirm, Select, Space, Table } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { type DepartmentOut, useCreateDepartment, useDeleteDepartment, useDepartments, useEmployees, useSetManager, useUpdateDepartment } from './api'
import { handle } from '../../shared/async'

export default function DepartmentsPage() {
  const { t } = useTranslation()
  const departments = useDepartments()
  const employees = useEmployees()
  const create = useCreateDepartment()
  const update = useUpdateDepartment()
  const remove = useDeleteDepartment()
  const setManager = useSetManager()
  const [editing, setEditing] = useState<DepartmentOut | 'new' | null>(null)
  const [form] = Form.useForm<{ code: string; name: string }>()
  const employeeName = (id: string | null) => {
    const e = employees.data?.items.find((x) => x.id === id)
    return e ? `${e.first_name} ${e.last_name}` : t('admin.noManager')
  }
  const submit = async () => {
    const v = await form.validateFields()
    if (editing === 'new') await create.mutateAsync(v)
    else if (editing) await update.mutateAsync({ id: editing.id, body: v })
    setEditing(null)
    form.resetFields()
  }
  return (
    <PageHeader title={t('admin.departments')} extra={<Button type="primary" onClick={() => setEditing('new')} data-testid="new-department">{t('admin.newDepartment')}</Button>}>
      {departments.error ? <ProblemAlert error={departments.error} /> : null}
      <Table<DepartmentOut>
        rowKey="id"
        loading={departments.isPending}
        dataSource={departments.data ?? []}
        data-testid="departments-table"
        columns={[
          { title: t('common.code'), dataIndex: 'code' },
          { title: t('common.name'), dataIndex: 'name' },
          { title: t('admin.manager'), dataIndex: 'manager_employee_id', render: (v: string | null, d) => (
              <Select
                size="small"
                style={{ minWidth: 220 }}
                value={v ?? undefined}
                placeholder={employeeName(null)}
                allowClear
                onChange={(id) => setManager.mutate({ id: d.id, employeeId: id ?? null })}
                options={(employees.data?.items ?? []).filter((e) => e.department_id === d.id && e.roles.includes('manager')).map((e) => ({ value: e.id, label: `${e.first_name} ${e.last_name}` }))}
              />
            ) },
          { title: t('common.actions'), render: (_, d) => (
              <Space>
                <Button size="small" onClick={() => { form.setFieldsValue({ code: d.code, name: d.name }); setEditing(d) }}>{t('common.edit')}</Button>
                <Popconfirm title={t('admin.deleteDepartmentConfirm')} onConfirm={() => remove.mutate(d.id)}>
                  <Button size="small" danger>{t('common.delete')}</Button>
                </Popconfirm>
              </Space>
            ) },
        ]}
      />
      <Modal open={Boolean(editing)} title={editing === 'new' ? t('admin.newDepartment') : t('admin.editDepartment')} onCancel={() => setEditing(null)} onOk={handle(submit)} confirmLoading={create.isPending || update.isPending} destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="code" label={t('common.code')} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="name" label={t('common.name')} rules={[{ required: true }]}><Input /></Form.Item>
        </Form>
      </Modal>
    </PageHeader>
  )
}
