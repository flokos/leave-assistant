import { Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { usePrincipal } from '../../auth/usePrincipal'
import { handle } from '../../shared/async'
import { formatDate, formatDateTime } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import {
  DOCUMENT_CLASSES,
  RETENTION_ACTIONS,
  type RetentionDecision,
  type RetentionItemOut,
  type RetentionPolicyOut,
  useCreateRetentionPolicy,
  useDecideRetentionItem,
  useDeleteRetentionPolicy,
  useRetentionItems,
  useRetentionPolicies,
  useUpdateRetentionPolicy,
} from './api'

type ItemStatus = 'pending' | 'approved_delete' | 'retained' | 'deleted'

function ReviewItems() {
  const { t, i18n } = useTranslation()
  const [status, setStatus] = useState<ItemStatus>('pending')
  const items = useRetentionItems(status)
  const decide = useDecideRetentionItem()
  const [active, setActive] = useState<{ item: RetentionItemOut; decision: RetentionDecision } | null>(null)
  const [form] = Form.useForm<{ comment?: string }>()
  const submit = async () => {
    if (!active) return
    const values = await form.validateFields()
    await decide.mutateAsync({ itemId: active.item.id, decision: active.decision, comment: values.comment })
    form.resetFields()
    setActive(null)
  }
  return (
    <Card
      title={t('retention.items')}
      extra={<Select value={status} onChange={setStatus} style={{ minWidth: 180 }} options={(['pending', 'approved_delete', 'retained', 'deleted'] as ItemStatus[]).map((s) => ({ value: s, label: t(`retention.statuses.${s}`) }))} />}
      data-testid="retention-items"
    >
      {items.error ? <ProblemAlert error={items.error} /> : null}
      <Table<RetentionItemOut>
        rowKey="id"
        size="small"
        loading={items.isPending}
        dataSource={items.data ?? []}
        locale={{ emptyText: t('retention.noItems') }}
        data-testid="retention-items-table"
        columns={[
          { title: t('retention.subject'), dataIndex: 'label' },
          { title: t('common.type'), dataIndex: 'subject_type', render: (v: string) => t(`retention.subjects.${v}`, { defaultValue: v }) },
          { title: t('retention.eligibleSince'), dataIndex: 'eligible_since', render: (v: string) => formatDate(v, i18n.language) },
          { title: t('common.status'), dataIndex: 'status', render: (v: string) => <Tag data-testid={`retention-status-${v}`}>{t(`retention.statuses.${v}`)}</Tag> },
          { title: t('common.comment'), dataIndex: 'comment', render: (v: string | null) => v ?? '—' },
          { title: t('common.when'), dataIndex: 'decided_at', render: (v: string | null) => formatDateTime(v, i18n.language) },
          {
            title: t('common.actions'),
            render: (_, row) =>
              row.status === 'pending' ? (
                <Space>
                  <Button size="small" onClick={() => setActive({ item: row, decision: 'retained' })} data-testid={`retain-${row.id}`}>
                    {t('retention.retain')}
                  </Button>
                  <Button size="small" danger onClick={() => setActive({ item: row, decision: 'approved_delete' })} data-testid={`approve-delete-${row.id}`}>
                    {t('retention.approveDelete')}
                  </Button>
                </Space>
              ) : (
                '—'
              ),
          },
        ]}
      />
      <Modal
        open={Boolean(active)}
        title={active ? t(`retention.${active.decision === 'retained' ? 'retainTitle' : 'deleteTitle'}`, { label: active.item.label }) : ''}
        onCancel={() => setActive(null)}
        onOk={handle(submit)}
        okButtonProps={{ danger: active?.decision === 'approved_delete' }}
        confirmLoading={decide.isPending}
        destroyOnHidden
      >
        {decide.error ? <ProblemAlert error={decide.error} /> : null}
        <Form form={form} layout="vertical">
          <Form.Item name="comment" label={t('common.comment')}>
            <Input.TextArea rows={2} maxLength={1000} data-testid="retention-comment" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

function Policies({ canManage }: { canManage: boolean }) {
  const { t } = useTranslation()
  const policies = useRetentionPolicies()
  const create = useCreateRetentionPolicy()
  const update = useUpdateRetentionPolicy()
  const remove = useDeleteRetentionPolicy()
  const [editing, setEditing] = useState<RetentionPolicyOut | 'new' | null>(null)
  const [form] = Form.useForm<{ document_class: (typeof DOCUMENT_CLASSES)[number]; retention_days: number; action: (typeof RETENTION_ACTIONS)[number] }>()
  const submit = async () => {
    const values = await form.validateFields()
    if (editing === 'new') await create.mutateAsync({ document_class: values.document_class, retention_days: values.retention_days, action: values.action, enabled: true })
    else if (editing) await update.mutateAsync({ policyId: editing.id, body: { retention_days: values.retention_days, action: values.action } })
    form.resetFields()
    setEditing(null)
  }
  return (
    <Card
      title={t('retention.policies')}
      extra={
        canManage && (
          <Button type="primary" onClick={() => { form.resetFields(); setEditing('new') }} data-testid="new-retention-policy">
            {t('retention.newPolicy')}
          </Button>
        )
      }
      data-testid="retention-policies"
    >
      <Typography.Paragraph type="secondary">{t('retention.disclaimer')}</Typography.Paragraph>
      {policies.error ? <ProblemAlert error={policies.error} /> : null}
      <Table<RetentionPolicyOut>
        rowKey="id"
        size="small"
        pagination={false}
        loading={policies.isPending}
        dataSource={policies.data ?? []}
        data-testid="retention-policies-table"
        columns={[
          { title: t('retention.documentClass'), dataIndex: 'document_class', render: (v: string) => t(`retention.classes.${v}`, { defaultValue: v }) },
          { title: t('retention.days'), dataIndex: 'retention_days' },
          { title: t('retention.action'), dataIndex: 'action', render: (v: string) => t(`retention.actions.${v}`, { defaultValue: v }) },
          {
            title: t('common.active'),
            render: (_, row) => (
              <Switch size="small" checked={row.enabled} disabled={!canManage} onChange={(enabled) => update.mutate({ policyId: row.id, body: { enabled } })} data-testid={`policy-enabled-${row.document_class}`} />
            ),
          },
          ...(canManage
            ? [
                {
                  title: t('common.actions'),
                  render: (_: unknown, row: RetentionPolicyOut) => (
                    <Space>
                      <Button size="small" onClick={() => { form.setFieldsValue({ document_class: row.document_class as never, retention_days: row.retention_days, action: row.action as never }); setEditing(row) }}>
                        {t('common.edit')}
                      </Button>
                      <Popconfirm title={t('retention.deletePolicyConfirm')} onConfirm={() => remove.mutate(row.id)}>
                        <Button size="small" danger>
                          {t('common.delete')}
                        </Button>
                      </Popconfirm>
                    </Space>
                  ),
                },
              ]
            : []),
        ]}
      />
      <Modal open={Boolean(editing)} title={editing === 'new' ? t('retention.newPolicy') : t('retention.editPolicy')} onCancel={() => setEditing(null)} onOk={handle(submit)} confirmLoading={create.isPending || update.isPending} destroyOnHidden>
        {create.error ? <ProblemAlert error={create.error} /> : null}
        <Form form={form} layout="vertical" initialValues={{ retention_days: 365, action: 'delete_after_review' }}>
          <Form.Item name="document_class" label={t('retention.documentClass')} rules={[{ required: true }]}>
            <Select disabled={editing !== 'new'} options={DOCUMENT_CLASSES.map((c) => ({ value: c, label: t(`retention.classes.${c}`) }))} data-testid="policy-class" />
          </Form.Item>
          <Form.Item name="retention_days" label={t('retention.days')} rules={[{ required: true }]}>
            <InputNumber min={0} max={3650} style={{ width: '100%' }} data-testid="policy-days" />
          </Form.Item>
          <Form.Item name="action" label={t('retention.action')} rules={[{ required: true }]}>
            <Select options={RETENTION_ACTIONS.map((a) => ({ value: a, label: t(`retention.actions.${a}`) }))} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

/** Retention (Q-08): HR decides review items; company administrators manage the policies. */
export default function RetentionPage() {
  const { t } = useTranslation()
  const principal = usePrincipal()
  return (
    <PageHeader title={t('retention.title')}>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        {principal.hasRole('hr_admin') && <ReviewItems />}
        <Policies canManage={principal.hasRole('company_admin')} />
      </Space>
    </PageHeader>
  )
}
