import { PaperClipOutlined } from '@ant-design/icons'
import { Button, Form, Input, Modal, Popconfirm, Space, Table, Typography, Upload } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { handle } from '../../shared/async'
import { formatDateTime } from '../../shared/dates'
import { ProblemAlert } from '../../shared/ProblemAlert'
import {
  type SupportingDocumentOut,
  useDeleteSupportingDocument,
  useDownloadSupportingDocument,
  useSupportingDocuments,
  useUploadSupportingDocument,
} from './supportingApi'

const ACCEPT = '.pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg'
const CLOSED = new Set(['REJECTED', 'CANCELLED', 'WITHDRAWN'])

/**
 * Supporting documents of one leave request (doc 08 §9): the owner attaches and downloads their own files;
 * HR lists, downloads with a reason and deletes. Managers never see this panel (the API answers 404 anyway).
 */
export function SupportingDocuments({ requestId, requestStatus, isOwner, isHr, open }: { requestId: string; requestStatus: string; isOwner: boolean; isHr: boolean; open?: (url: string) => void }) {
  const { t, i18n } = useTranslation()
  const documents = useSupportingDocuments(requestId, isOwner || isHr)
  const upload = useUploadSupportingDocument()
  const download = useDownloadSupportingDocument(open)
  const remove = useDeleteSupportingDocument()
  const [reasonFor, setReasonFor] = useState<{ document: SupportingDocumentOut; action: 'download' | 'delete' } | null>(null)
  const [form] = Form.useForm<{ reason: string }>()
  if (!isOwner && !isHr) return null

  const submitReason = async () => {
    if (!reasonFor) return
    const { reason } = await form.validateFields()
    if (reasonFor.action === 'download') await download.mutateAsync({ documentId: reasonFor.document.id, reason })
    else await remove.mutateAsync({ documentId: reasonFor.document.id, reason })
    form.resetFields()
    setReasonFor(null)
  }

  return (
    <div data-testid="supporting-documents">
      <Typography.Title level={5}>
        <PaperClipOutlined /> {t('supporting.title')}
      </Typography.Title>
      {documents.error ? <ProblemAlert error={documents.error} /> : null}
      {upload.error ? <ProblemAlert error={upload.error} /> : null}
      <Table<SupportingDocumentOut>
        size="small"
        rowKey="id"
        pagination={false}
        loading={documents.isPending}
        dataSource={documents.data ?? []}
        locale={{ emptyText: t('supporting.empty') }}
        data-testid="supporting-table"
        columns={[
          { title: t('documents.file'), dataIndex: 'original_filename' },
          { title: t('common.type'), dataIndex: 'mime' },
          { title: t('supporting.size'), dataIndex: 'size_bytes', render: (v: number) => `${Math.max(1, Math.round(v / 1024))} KB` },
          { title: t('supporting.uploadedAt'), dataIndex: 'created_at', render: (v: string) => formatDateTime(v, i18n.language) },
          {
            title: t('common.actions'),
            render: (_, d) => (
              <Space>
                <Button
                  size="small"
                  loading={download.isPending}
                  onClick={isHr && !isOwner ? () => setReasonFor({ document: d, action: 'download' }) : handle(() => download.mutateAsync({ documentId: d.id }))}
                  data-testid={`supporting-download-${d.id}`}
                >
                  {t('documents.download')}
                </Button>
                {isHr && (
                  <Popconfirm title={t('supporting.deleteConfirm')} onConfirm={() => setReasonFor({ document: d, action: 'delete' })}>
                    <Button size="small" danger data-testid={`supporting-delete-${d.id}`}>
                      {t('common.delete')}
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            ),
          },
        ]}
      />
      {(isOwner || isHr) && !CLOSED.has(requestStatus) && (
        <div data-testid="supporting-upload" style={{ marginTop: 8 }}>
          <Upload
            accept={ACCEPT}
            showUploadList={false}
            beforeUpload={(file) => {
              void upload.mutateAsync({ requestId, file })
              return false
            }}
          >
            <Button loading={upload.isPending}>{t('supporting.attach')}</Button>
          </Upload>
          <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
            {t('supporting.formats')}
          </Typography.Text>
        </div>
      )}
      <Modal
        open={Boolean(reasonFor)}
        title={reasonFor?.action === 'delete' ? t('supporting.deleteTitle') : t('supporting.reasonTitle')}
        onCancel={() => setReasonFor(null)}
        onOk={handle(submitReason)}
        confirmLoading={download.isPending || remove.isPending}
        destroyOnHidden
      >
        <Typography.Paragraph type="secondary">{t('supporting.reasonHint')}</Typography.Paragraph>
        {download.error ? <ProblemAlert error={download.error} /> : null}
        <Form form={form} layout="vertical">
          <Form.Item name="reason" label={t('common.reason')} rules={[{ required: true, message: t('common.required') }]}>
            <Input.TextArea rows={2} maxLength={500} data-testid="supporting-reason" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
