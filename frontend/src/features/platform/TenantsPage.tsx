import { Alert, Button, Form, Input, Modal, Popconfirm, Select, Space, Table, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDateTime } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { type TenantOut, useBootstrapAdmin, useCreateTenant, useSetTenantStatus, useTenants } from './api'
import { handle } from '../../shared/async'

export default function TenantsPage() {
  const { t, i18n } = useTranslation()
  const tenants = useTenants()
  const create = useCreateTenant()
  const setStatus = useSetTenantStatus()
  const bootstrap = useBootstrapAdmin()
  const [creating, setCreating] = useState(false)
  const [bootstrapping, setBootstrapping] = useState<TenantOut | null>(null)
  const [form] = Form.useForm()
  const [bootForm] = Form.useForm()
  const submitCreate = async () => {
    const v = await form.validateFields()
    await create.mutateAsync(v)
    setCreating(false)
    form.resetFields()
  }
  const submitBootstrap = async () => {
    if (!bootstrapping) return
    const v = await bootForm.validateFields()
    await bootstrap.mutateAsync({ id: bootstrapping.id, body: v })
  }
  return (
    <PageHeader title={t('platform.tenants')} extra={<Button type="primary" onClick={() => setCreating(true)} data-testid="new-tenant">{t('platform.newTenant')}</Button>}>
      {tenants.error ? <ProblemAlert error={tenants.error} /> : null}
      <Table<TenantOut>
        rowKey="id"
        loading={tenants.isPending}
        dataSource={tenants.data?.items ?? []}
        data-testid="tenants-table"
        columns={[
          { title: t('platform.slug'), dataIndex: 'slug' },
          { title: t('common.name'), dataIndex: 'name' },
          { title: t('common.status'), dataIndex: 'status', render: (v: string) => <StatusTag status={v} /> },
          { title: t('platform.defaultLocale'), dataIndex: 'default_locale' },
          { title: t('platform.timezone'), dataIndex: 'timezone' },
          { title: t('common.created'), dataIndex: 'created_at', render: (v: string) => formatDateTime(v, i18n.language) },
          { title: t('common.actions'), render: (_, tenant) => (
              <Space>
                <Button size="small" onClick={() => { bootstrap.reset(); setBootstrapping(tenant) }}>{t('platform.bootstrap')}</Button>
                {tenant.status === 'active' ? (
                  <Popconfirm title={t('platform.suspendConfirm')} onConfirm={() => setStatus.mutate({ id: tenant.id, action: 'suspend' })}>
                    <Button size="small" danger>{t('platform.suspend')}</Button>
                  </Popconfirm>
                ) : (
                  <Button size="small" onClick={() => setStatus.mutate({ id: tenant.id, action: 'resume' })}>{t('platform.resume')}</Button>
                )}
              </Space>
            ) },
        ]}
      />
      <Modal open={creating} title={t('platform.newTenant')} onCancel={() => setCreating(false)} onOk={handle(submitCreate)} confirmLoading={create.isPending} destroyOnHidden>
        {create.error ? <ProblemAlert error={create.error} /> : null}
        <Form form={form} layout="vertical" initialValues={{ default_locale: 'en', timezone: 'Europe/Athens' }}>
          <Form.Item name="slug" label={t('platform.slug')} rules={[{ required: true, pattern: /^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$/ }]}><Input /></Form.Item>
          <Form.Item name="name" label={t('common.name')} rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="default_locale" label={t('platform.defaultLocale')}><Select options={[{ value: 'en', label: 'English' }, { value: 'el', label: 'Ελληνικά' }]} /></Form.Item>
          <Form.Item name="timezone" label={t('platform.timezone')} rules={[{ required: true }]}><Input /></Form.Item>
        </Form>
      </Modal>
      <Modal open={Boolean(bootstrapping)} title={bootstrapping ? t('platform.bootstrapTitle', { name: bootstrapping.name }) : ''} onCancel={() => setBootstrapping(null)} onOk={handle(submitBootstrap)} confirmLoading={bootstrap.isPending} okButtonProps={{ disabled: Boolean(bootstrap.data) }} destroyOnHidden>
        {bootstrap.error ? <ProblemAlert error={bootstrap.error} /> : null}
        {bootstrap.data ? (
          <Alert type="success" showIcon message={t('platform.bootstrapDone', { username: bootstrap.data.username })} description={<Typography.Text code copyable>{bootstrap.data.temporary_password}</Typography.Text>} />
        ) : (
          <Form form={bootForm} layout="vertical" initialValues={{ locale: 'en' }}>
            <Form.Item name="email" label={t('common.email')} rules={[{ required: true, type: 'email' }]}><Input /></Form.Item>
            <Form.Item name="first_name" label={t('common.firstName')} rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="last_name" label={t('common.lastName')} rules={[{ required: true }]}><Input /></Form.Item>
            <Form.Item name="locale" label={t('common.locale')}><Select options={[{ value: 'en', label: 'English' }, { value: 'el', label: 'Ελληνικά' }]} /></Form.Item>
          </Form>
        )}
      </Modal>
    </PageHeader>
  )
}
