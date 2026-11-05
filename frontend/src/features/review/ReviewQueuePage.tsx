import { Button, Descriptions, Form, Input, List, Modal, Space, Table, Tag, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { handle } from '../../shared/async'
import { formatDateTime } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { type ReviewAction, type ReviewItemOut, useReviewDecision, useReviewQueue } from './api'

const TIER_COLORS: Record<string, string> = { low: 'green', medium: 'orange', high: 'red' }
const NEEDS_COMMENT = new Set<ReviewAction>(['reject', 'delete'])

export function TierTag({ tier, score }: { tier: string; score: number }) {
  const { t } = useTranslation()
  return (
    <Tag color={TIER_COLORS[tier] ?? 'default'} data-testid={`tier-${tier}`}>
      {t(`review.tiers.${tier}`)} · {score.toFixed(2)}
    </Tag>
  )
}

/** HR / company administrators decide on documents the pipeline held back (doc 08 §8.4). */
export default function ReviewQueuePage() {
  const { t, i18n } = useTranslation()
  const queue = useReviewQueue()
  const decide = useReviewDecision()
  const [active, setActive] = useState<{ item: ReviewItemOut; action: ReviewAction } | null>(null)
  const [form] = Form.useForm<{ comment?: string }>()

  const submit = async () => {
    if (!active) return
    const values = await form.validateFields()
    await decide.mutateAsync({ documentId: active.item.document_id, versionNo: active.item.version.version_no, action: active.action, comment: values.comment })
    form.resetFields()
    setActive(null)
  }

  return (
    <PageHeader title={t('review.title')} extra={<Button onClick={() => void queue.refetch()}>{t('common.refresh')}</Button>}>
      <Typography.Paragraph type="secondary">{t('review.intro')}</Typography.Paragraph>
      {queue.error ? <ProblemAlert error={queue.error} /> : null}
      <Table<ReviewItemOut>
        rowKey={(row) => row.version.id}
        loading={queue.isPending}
        dataSource={queue.data ?? []}
        locale={{ emptyText: t('review.empty') }}
        data-testid="review-table"
        expandable={{
          expandedRowRender: (row) => (
            <Space direction="vertical" style={{ width: '100%' }} data-testid={`findings-${row.version.id}`}>
              <List
                size="small"
                header={<strong>{t('review.findings')}</strong>}
                dataSource={row.findings}
                locale={{ emptyText: t('review.noFindings') }}
                renderItem={(f) => (
                  <List.Item>
                    <Space direction="vertical" size={0}>
                      <Space>
                        <Tag>{t(`review.detectors.${f.detector}`, { defaultValue: f.detector })}</Tag>
                        <Tag color="volcano">{t(`review.families.${f.family}`, { defaultValue: f.family })}</Tag>
                        <span>{f.score.toFixed(2)}</span>
                        {f.chunk !== null && f.chunk !== undefined ? <span>{t('review.chunk', { n: f.chunk })}</span> : null}
                      </Space>
                      {f.excerpt ? <Typography.Text code>{f.excerpt}</Typography.Text> : null}
                    </Space>
                  </List.Item>
                )}
              />
              <Descriptions size="small" column={3} title={t('review.pii')}>
                {Object.entries((row.pii_summary.entities as Record<string, number> | undefined) ?? {}).map(([k, v]) => (
                  <Descriptions.Item key={k} label={k}>
                    {v}
                  </Descriptions.Item>
                ))}
                {!Object.keys((row.pii_summary.entities as Record<string, number> | undefined) ?? {}).length && (
                  <Descriptions.Item label={t('review.pii')}>{t('review.noPii')}</Descriptions.Item>
                )}
              </Descriptions>
            </Space>
          ),
        }}
        columns={[
          { title: t('documents.docTitle'), dataIndex: 'title' },
          { title: t('documents.category'), dataIndex: 'category', render: (v: string) => t(`documents.categories.${v}`) },
          { title: 'v', render: (_, row) => row.version.version_no },
          { title: t('common.status'), render: (_, row) => <StatusTag status={row.version.status} /> },
          { title: t('review.risk'), render: (_, row) => <TierTag tier={row.version.risk_tier} score={row.version.risk_score} /> },
          { title: t('review.findings'), render: (_, row) => row.findings.length },
          { title: t('documents.processedAt'), render: (_, row) => formatDateTime(row.version.processed_at, i18n.language) },
          {
            title: t('common.actions'),
            render: (_, row) => (
              <Space wrap>
                <Button size="small" type="primary" onClick={() => setActive({ item: row, action: 'release' })} data-testid={`release-${row.version.version_no}`}>
                  {t('review.actions.release')}
                </Button>
                <Button size="small" onClick={() => setActive({ item: row, action: 'inspect' })} data-testid={`inspect-${row.version.version_no}`}>
                  {t('review.actions.inspect')}
                </Button>
                {row.version.status === 'PENDING_REVIEW' && (
                  <Button size="small" danger onClick={() => setActive({ item: row, action: 'reject' })} data-testid={`reject-${row.version.version_no}`}>
                    {t('review.actions.reject')}
                  </Button>
                )}
                {row.version.status === 'QUARANTINED' && (
                  <Button size="small" danger onClick={() => setActive({ item: row, action: 'delete' })} data-testid={`delete-${row.version.version_no}`}>
                    {t('review.actions.delete')}
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Modal
        open={Boolean(active)}
        title={active ? t('review.decisionTitle', { action: t(`review.actions.${active.action}`), title: active.item.title }) : ''}
        onCancel={() => setActive(null)}
        onOk={handle(submit)}
        okText={active ? t(`review.actions.${active.action}`) : undefined}
        okButtonProps={{ danger: active ? NEEDS_COMMENT.has(active.action) : false }}
        confirmLoading={decide.isPending}
        destroyOnHidden
      >
        {decide.error ? <ProblemAlert error={decide.error} /> : null}
        <Typography.Paragraph type="secondary">{active ? t(`review.hints.${active.action}`) : ''}</Typography.Paragraph>
        <Form form={form} layout="vertical">
          <Form.Item name="comment" label={t('common.comment')} rules={active && NEEDS_COMMENT.has(active.action) ? [{ required: true, message: t('review.commentRequired') }] : []}>
            <Input.TextArea rows={3} maxLength={1000} data-testid="review-comment" />
          </Form.Item>
        </Form>
      </Modal>
    </PageHeader>
  )
}
