import { Button, Descriptions, Drawer, Popconfirm, Space, Table, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { handle } from '../../shared/async'
import { formatDateTime } from '../../shared/dates'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { type VersionOut, downloadVersion, useDeleteDocument, useDocument, useReprocessVersion, useRetireVersion } from './api'
import { UploadModal } from './UploadModal'

export function DocumentDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const detail = useDocument(id)
  const retire = useRetireVersion()
  const reprocess = useReprocessVersion()
  const remove = useDeleteDocument()
  const [versionModal, setVersionModal] = useState(false)
  const d = detail.data
  return (
    <Drawer open={Boolean(id)} onClose={onClose} width={720} title={d?.title ?? t('documents.detail')}>
      <div data-testid="document-detail">
        {detail.error ? <ProblemAlert error={detail.error} /> : null}
        {d && (
          <Space direction="vertical" size="large" style={{ width: '100%' }}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label={t('documents.category')}>{t(`documents.categories.${d.category}`)}</Descriptions.Item>
              <Descriptions.Item label={t('documents.activeVersion')}>
                {d.active_version ? (
                  <Space>
                    <StatusTag status={d.active_version.status} />
                    <span>v{d.active_version.version_no}</span>
                    <span>{d.active_version.pages ? `${d.active_version.pages} ${t('documents.pages')}` : ''}</span>
                    <span>{d.active_version.language ?? ''}</span>
                  </Space>
                ) : (
                  t('documents.noActiveVersion')
                )}
              </Descriptions.Item>
              <Descriptions.Item label={t('common.updated')}>{formatDateTime(d.updated_at, i18n.language)}</Descriptions.Item>
            </Descriptions>
            {d.can_manage && (
              <Space>
                <Button onClick={() => setVersionModal(true)} data-testid="new-version">
                  {t('documents.newVersion')}
                </Button>
                <Popconfirm title={t('documents.deleteConfirm')} onConfirm={handle(async () => { await remove.mutateAsync({ documentId: d.id, version: d.version }); onClose() })}>
                  <Button danger loading={remove.isPending} data-testid="delete-document">
                    {t('common.delete')}
                  </Button>
                </Popconfirm>
              </Space>
            )}
            <div>
              <Typography.Title level={5}>{t('documents.versions')}</Typography.Title>
              <Table<VersionOut>
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={d.versions}
                data-testid="versions-table"
                columns={[
                  { title: 'v', dataIndex: 'version_no' },
                  { title: t('common.status'), dataIndex: 'status', render: (v: string) => <StatusTag status={v} /> },
                  { title: t('documents.file'), dataIndex: 'original_filename' },
                  { title: t('documents.pages'), dataIndex: 'pages', render: (v: number | null) => v ?? '—' },
                  { title: t('common.locale'), dataIndex: 'language', render: (v: string | null) => v ?? '—' },
                  { title: t('documents.processedAt'), dataIndex: 'processed_at', render: (v: string | null) => formatDateTime(v, i18n.language) },
                  { title: t('documents.error'), dataIndex: 'processing_error', render: (v: string | null) => v ?? '—' },
                  {
                    title: t('common.actions'),
                    render: (_, v) => (
                      <Space>
                        <Button size="small" onClick={handle(() => downloadVersion(d.id, v.version_no, v.original_filename))} data-testid={`download-${v.version_no}`}>
                          {t('documents.download')}
                        </Button>
                        {d.can_manage && v.status === 'ACTIVE' && (
                          <Popconfirm title={t('documents.retireConfirm')} onConfirm={() => retire.mutate({ documentId: d.id, versionNo: v.version_no })}>
                            <Button size="small" danger data-testid={`retire-${v.version_no}`}>
                              {t('documents.retire')}
                            </Button>
                          </Popconfirm>
                        )}
                        {d.can_manage && v.status === 'FAILED' && (
                          <Button size="small" onClick={() => reprocess.mutate({ documentId: d.id, versionNo: v.version_no })} data-testid={`reprocess-${v.version_no}`}>
                            {t('documents.reprocess')}
                          </Button>
                        )}
                      </Space>
                    ),
                  },
                ]}
              />
            </div>
          </Space>
        )}
      </div>
      <UploadModal open={versionModal} onClose={() => setVersionModal(false)} documentId={id} />
    </Drawer>
  )
}
