import { Alert, Button, Checkbox, DatePicker, Form, Input, Modal, Popconfirm, Select, Space, Table, Typography } from 'antd'
import dayjs from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toIso } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { type EmployeeOut, useCreateEmployee, useDeactivateEmployee, useDepartments, useEmployees, useSetRoles, useUpdateEmployee } from './api'
import { handle } from '../../shared/async'

const ROLES = ['employee', 'manager', 'hr_admin', 'company_admin'] as const

export default function EmployeesPage() {
  const { t } = useTranslation()
  const employees = useEmployees()
  const departments = useDepartments()
  const create = useCreateEmployee()
  const update = useUpdateEmployee()
  const deactivate = useDeactivateEmployee()
  const setRoles = useSetRoles()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<EmployeeOut | null>(null)
  const [rolesFor, setRolesFor] = useState<EmployeeOut | null>(null)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const [rolesForm] = Form.useForm<{ roles: (typeof ROLES)[number][] }>()
  const deptName = (id: string) => departments.data?.find((d) => d.id === id)?.name ?? id

  const submitCreate = async () => {
    const v = await createForm.validateFields()
    await create.mutateAsync({ ...v, hire_date: toIso(v.hire_date), roles: v.roles })
    createForm.resetFields()
  }
  const submitEdit = async () => {
    if (!editing) return
    const v = await editForm.validateFields()
    await update.mutateAsync({ id: editing.id, body: { ...v, hire_date: v.hire_date ? toIso(v.hire_date) : undefined } })
    setEditing(null)
  }
  const submitRoles = async () => {
    if (!rolesFor) return
    const v = await rolesForm.validateFields()
    await setRoles.mutateAsync({ id: rolesFor.id, roles: v.roles })
    setRolesFor(null)
  }

  return (
    <PageHeader title={t('admin.employees')} extra={<Button type="primary" onClick={() => setCreating(true)} data-testid="new-employee">{t('admin.newEmployee')}</Button>}>
      {employees.error ? <ProblemAlert error={employees.error} /> : null}
      <Table<EmployeeOut>
        rowKey="id"
        loading={employees.isPending}
        dataSource={employees.data?.items ?? []}
        data-testid="employees-table"
        columns={[
          { title: t('common.employeeNumber'), dataIndex: 'employee_number' },
          { title: t('common.name'), render: (_, e) => `${e.first_name} ${e.last_name}` },
          { title: t('common.email'), dataIndex: 'email' },
          { title: t('common.department'), dataIndex: 'department_id', render: (v: string) => deptName(v) },
          { title: t('admin.rolesTitle'), dataIndex: 'roles', render: (roles: string[]) => roles.map((r) => t(`roles.${r}`)).join(', ') },
          { title: t('common.status'), dataIndex: 'status', render: (v: string) => <StatusTag status={v} /> },
          { title: t('common.actions'), render: (_, e) => (
              <Space>
                <Button size="small" onClick={() => { editForm.setFieldsValue({ ...e, hire_date: dayjs(e.hire_date) }); setEditing(e) }}>{t('common.edit')}</Button>
                <Button size="small" onClick={() => { rolesForm.setFieldsValue({ roles: e.roles }); setRolesFor(e) }}>{t('admin.setRoles')}</Button>
                {e.status === 'active' && (
                  <Popconfirm title={t('admin.deactivateConfirm')} onConfirm={() => deactivate.mutate(e.id)}>
                    <Button size="small" danger>{t('admin.deactivate')}</Button>
                  </Popconfirm>
                )}
              </Space>
            ) },
        ]}
      />
      <Modal open={creating} title={t('admin.newEmployee')} onCancel={() => { setCreating(false); create.reset() }} onOk={handle(submitCreate)} confirmLoading={create.isPending} okText={t('common.create')} destroyOnHidden={false}>
        {create.data ? (
          <Alert
            type="success"
            showIcon
            data-testid="temporary-password"
            message={`${create.data.employee.email}`}
            description={
              <>
                <div>{t('admin.temporaryPasswordHint')}</div>
                <Typography.Text code copyable>{create.data.temporary_password ?? ''}</Typography.Text>
              </>
            }
          />
        ) : (
          <Form form={createForm} layout="vertical" initialValues={{ locale: 'en', roles: ['employee'] }}>
            <Form.Item name="email" label={t('common.email')} rules={[{ required: true, type: 'email' }]}><Input data-testid="employee-email" /></Form.Item>
            <Form.Item name="first_name" label={t('common.firstName')} rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="last_name" label={t('common.lastName')} rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="employee_number" label={t('common.employeeNumber')} rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="department_id" label={t('common.department')} rules={[{ required: true }]}>
              <Select options={(departments.data ?? []).map((d) => ({ value: d.id, label: `${d.code} · ${d.name}` }))} />
            </Form.Item>
            <Form.Item name="locale" label={t('common.locale')}><Select options={[{ value: 'en', label: 'English' }, { value: 'el', label: 'Ελληνικά' }]} /></Form.Item>
            <Form.Item name="hire_date" label={t('common.hireDate')} rules={[{ required: true }]}><DatePicker format="YYYY-MM-DD" style={{ width: '100%' }} /></Form.Item>
            <Form.Item name="roles" label={t('admin.rolesTitle')} rules={[{ required: true }]}>
              <Checkbox.Group options={ROLES.map((r) => ({ value: r, label: t(`roles.${r}`) }))} />
            </Form.Item>
            {create.error ? <ProblemAlert error={create.error} /> : null}
          </Form>
        )}
      </Modal>
      <Modal open={Boolean(editing)} title={t('admin.editEmployee')} onCancel={() => setEditing(null)} onOk={handle(submitEdit)} confirmLoading={update.isPending} destroyOnHidden>
        <Form form={editForm} layout="vertical">
          <Form.Item name="first_name" label={t('common.firstName')} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="last_name" label={t('common.lastName')} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="employee_number" label={t('common.employeeNumber')}><Input /></Form.Item>
          <Form.Item name="department_id" label={t('common.department')}>
            <Select options={(departments.data ?? []).map((d) => ({ value: d.id, label: `${d.code} · ${d.name}` }))} />
          </Form.Item>
          <Form.Item name="locale" label={t('common.locale')}><Select options={[{ value: 'en', label: 'English' }, { value: 'el', label: 'Ελληνικά' }]} /></Form.Item>
          <Form.Item name="hire_date" label={t('common.hireDate')}><DatePicker format="YYYY-MM-DD" style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="manager_override_employee_id" label={t('admin.managerOverride')}>
            <Select allowClear options={(employees.data?.items ?? []).filter((e) => e.id !== editing?.id).map((e) => ({ value: e.id, label: `${e.first_name} ${e.last_name}` }))} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal open={Boolean(rolesFor)} title={t('admin.setRoles')} onCancel={() => setRolesFor(null)} onOk={handle(submitRoles)} confirmLoading={setRoles.isPending} destroyOnHidden>
        <Form form={rolesForm} layout="vertical">
          <Form.Item name="roles" rules={[{ required: true }]}>
            <Checkbox.Group options={ROLES.map((r) => ({ value: r, label: t(`roles.${r}`) }))} />
          </Form.Item>
        </Form>
      </Modal>
    </PageHeader>
  )
}
