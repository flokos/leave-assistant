import { Button, DatePicker, Descriptions, Drawer, Form, Input, Modal, Popconfirm, Space, Table, Timeline, Typography } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDate, formatDateTime, formatRange, toIso } from '../../shared/dates'
import { formatDays } from '../../shared/days'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { type LeaveRequestOut, useCancelRequest, useLeaveTypeName, useModifyRequest, useRequest } from './api'
import { handle } from '../../shared/async'

const MODIFIABLE = new Set(['PENDING_MANAGER', 'PENDING_HR', 'APPROVED'])
const CANCELLABLE = new Set(['SUBMITTED', 'PENDING_MANAGER', 'PENDING_HR', 'APPROVED'])

export function RequestDetail({ id, onClose, readOnly }: { id: string | null; onClose: () => void; readOnly?: boolean }) {
  const { t, i18n } = useTranslation()
  const request = useRequest(id)
  const typeName = useLeaveTypeName()
  const modify = useModifyRequest()
  const cancel = useCancelRequest()
  const [modifying, setModifying] = useState(false)
  const [form] = Form.useForm<{ dates: [Dayjs, Dayjs]; reason?: string }>()
  const r: LeaveRequestOut | undefined = request.data

  const doModify = async () => {
    if (!r) return
    const values = await form.validateFields()
    await modify.mutateAsync({ id: r.id, body: { start_date: toIso(values.dates[0]), end_date: toIso(values.dates[1]), version: r.version, reason: values.reason ?? null } })
    setModifying(false)
  }

  return (
    <Drawer open={Boolean(id)} onClose={onClose} width={640} title={r ? `${r.request_number} · ${typeName(r.leave_type)}` : t('leave.details')}>
      <div data-testid="request-detail">
      {request.error ? <ProblemAlert error={request.error} /> : null}
      {r && (
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label={t('common.status')}>
              <StatusTag status={r.status} />
            </Descriptions.Item>
            <Descriptions.Item label={t('leave.dates')}>{formatRange(r.start_date, r.end_date, i18n.language)}</Descriptions.Item>
            <Descriptions.Item label={t('leave.workingDays')}>{formatDays(r.working_days)}</Descriptions.Item>
            <Descriptions.Item label={t('common.reason')}>{r.reason ?? '—'}</Descriptions.Item>
            <Descriptions.Item label={t('leave.requestedOn')}>{formatDateTime(r.submitted_at, i18n.language)}</Descriptions.Item>
            <Descriptions.Item label={t('common.version')}>{r.version}</Descriptions.Item>
          </Descriptions>
          {!readOnly && (
            <Space>
              {MODIFIABLE.has(r.status) && (
                <Button onClick={() => { form.setFieldsValue({ dates: [dayjs(r.start_date), dayjs(r.end_date)], reason: r.reason ?? undefined }); setModifying(true) }} data-testid="modify">
                  {t('leave.modify')}
                </Button>
              )}
              {CANCELLABLE.has(r.status) && (
                <Popconfirm title={t('leave.cancelConfirm')} onConfirm={() => cancel.mutate({ id: r.id, body: { version: r.version } })}>
                  <Button danger loading={cancel.isPending} data-testid="cancel-request">
                    {t('leave.cancel')}
                  </Button>
                </Popconfirm>
              )}
            </Space>
          )}
          <div>
            <Typography.Title level={5}>{t('leave.stepsTitle')}</Typography.Title>
            <Table
              size="small"
              rowKey="id"
              pagination={false}
              dataSource={r.steps}
              columns={[
                { title: t('approvals.step'), dataIndex: 'step_no' },
                { title: t('common.type'), dataIndex: 'approver_role', render: (v: string) => t(`stepRoles.${v}`) },
                { title: t('common.status'), dataIndex: 'status', render: (v: string) => <StatusTag status={v} /> },
                { title: t('leave.fallback'), dataIndex: 'fallback', render: (v: boolean) => (v ? t('common.yes') : '—') },
                { title: t('common.comment'), dataIndex: 'comment', render: (v: string | null) => v ?? '—' },
                { title: t('common.when'), dataIndex: 'decided_at', render: (v: string | null) => formatDateTime(v, i18n.language) },
              ]}
            />
          </div>
          <div>
            <Typography.Title level={5}>{t('leave.eventsTitle')}</Typography.Title>
            <Timeline items={r.events.map((e) => ({ key: e.id, children: `${formatDateTime(e.occurred_at, i18n.language)} · ${e.event_type} → ${t(`status.${e.to_status}`)}` }))} />
          </div>
        </Space>
      )}
      <Modal open={modifying} title={t('leave.modifyTitle')} onCancel={() => setModifying(false)} onOk={handle(doModify)} confirmLoading={modify.isPending} destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="dates" label={t('leave.selectDates')} rules={[{ required: true }]}>
            <DatePicker.RangePicker format="YYYY-MM-DD" style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="reason" label={t('common.reason')}>
            <Input.TextArea rows={2} maxLength={1000} />
          </Form.Item>
          {r && <Typography.Text type="secondary">{formatDate(r.start_date, i18n.language)} → …</Typography.Text>}
        </Form>
      </Modal>
      </div>
    </Drawer>
  )
}
