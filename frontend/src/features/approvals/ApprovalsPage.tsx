import { Button, Form, Input, Modal, Space, Table, Tag, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatRange } from '../../shared/dates'
import { formatDays } from '../../shared/days'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { useLeaveTypeName } from '../leave/api'
import { RequestDetail } from '../leave/RequestDetail'
import { type PendingItemOut, useDecide, usePendingApprovals } from './api'
import { handle } from '../../shared/async'

export function DecisionModal({ item, decision, onClose }: { item: PendingItemOut | null; decision: 'approve' | 'reject'; onClose: () => void }) {
  const { t } = useTranslation()
  const decide = useDecide()
  const [form] = Form.useForm<{ comment?: string }>()
  const submit = async () => {
    if (!item) return
    const values = await form.validateFields()
    await decide.mutateAsync({ stepId: item.step.id, decision, version: item.step.version, comment: values.comment })
    form.resetFields()
    onClose()
  }
  return (
    <Modal
      open={Boolean(item)}
      title={item ? t('approvals.decisionTitle', { number: item.request.request_number }) : ''}
      onCancel={onClose}
      onOk={handle(submit)}
      okText={decision === 'approve' ? t('approvals.approve') : t('approvals.reject')}
      okButtonProps={{ danger: decision === 'reject' }}
      confirmLoading={decide.isPending}
      destroyOnHidden
    >
      {decide.error ? <ProblemAlert error={decide.error} /> : null}
      <Form form={form} layout="vertical">
        <Form.Item name="comment" label={t('common.comment')} rules={decision === 'reject' ? [{ required: true, message: t('approvals.commentRequired') }] : []}>
          <Input.TextArea rows={3} maxLength={1000} data-testid="decision-comment" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default function ApprovalsPage() {
  const { t, i18n } = useTranslation()
  const pending = usePendingApprovals()
  const typeName = useLeaveTypeName()
  const [active, setActive] = useState<{ item: PendingItemOut; decision: 'approve' | 'reject' } | null>(null)
  const [detail, setDetail] = useState<string | null>(null)
  return (
    <PageHeader title={t('approvals.title')} extra={<Button onClick={() => void pending.refetch()}>{t('common.refresh')}</Button>}>
      {pending.error ? <ProblemAlert error={pending.error} /> : null}
      <Table<PendingItemOut>
        rowKey={(row) => row.step.id}
        loading={pending.isPending}
        dataSource={pending.data ?? []}
        locale={{ emptyText: t('approvals.noPending') }}
        data-testid="pending-table"
        columns={[
          { title: t('leave.requestNumber'), render: (_, row) => <a onClick={() => setDetail(row.request.id)}>{row.request.request_number}</a> },
          { title: t('approvals.requester'), render: (_, row) => `${row.requester.display_name} (${row.requester.employee_number})` },
          { title: t('common.type'), render: (_, row) => typeName(row.request.leave_type) },
          { title: t('leave.dates'), render: (_, row) => formatRange(row.request.start_date, row.request.end_date, i18n.language) },
          { title: t('leave.workingDays'), render: (_, row) => formatDays(row.request.working_days) },
          { title: t('common.reason'), render: (_, row) => row.reason ?? '—' },
          { title: t('approvals.step'), render: (_, row) => (
              <Space>
                {row.step.step_no} · {t(`stepRoles.${row.step.approver_role}`)}
                {row.fallback && <Tag color="purple">{t('approvals.fallbackTag')}</Tag>}
                <StatusTag status={row.request.status} />
              </Space>
            ) },
          { title: t('common.actions'), render: (_, row) => (
              <Space>
                <Button type="primary" size="small" onClick={() => setActive({ item: row, decision: 'approve' })} data-testid="approve">
                  {t('approvals.approve')}
                </Button>
                <Button danger size="small" onClick={() => setActive({ item: row, decision: 'reject' })} data-testid="reject">
                  {t('approvals.reject')}
                </Button>
              </Space>
            ) },
        ]}
      />
      <Typography.Text type="secondary">{t('leave.fallback')}: {t('approvals.fallbackTag')}</Typography.Text>
      <DecisionModal item={active?.item ?? null} decision={active?.decision ?? 'approve'} onClose={() => setActive(null)} />
      <RequestDetail id={detail} onClose={() => setDetail(null)} readOnly />
    </PageHeader>
  )
}
