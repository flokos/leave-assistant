import { Button, Card, Input, List, Select, Space, Table, Tag, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { usePrincipal } from '../../auth/usePrincipal'
import { formatDateTime } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { StatusTag } from '../../shared/StatusTag'
import { CATEGORIES, type DocumentOut, VERSION_STATUSES, currentVersion, useDocuments, useSearch } from './api'
import { DocumentDetail } from './DocumentDetail'
import { uploadCategoriesFor } from './permissions'
import { UploadModal } from './UploadModal'

export default function DocumentsPage() {
  const { t, i18n } = useTranslation()
  const principal = usePrincipal()
  const [category, setCategory] = useState<string | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [query, setQuery] = useState('')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const documents = useDocuments({ category, status })
  const search = useSearch(query)
  const canUpload = uploadCategoriesFor(principal.roles).length > 0
  return (
    <PageHeader
      title={t('documents.title')}
      extra={
        <Space wrap>
          <Input.Search
            allowClear
            placeholder={t('documents.searchPlaceholder')}
            onSearch={(value) => setQuery(value.trim())}
            onChange={(e) => e.target.value === '' && setQuery('')}
            style={{ width: 320 }}
            data-testid="document-search"
          />
          {canUpload && (
            <Button type="primary" onClick={() => setUploadOpen(true)} data-testid="upload-document">
              {t('documents.upload')}
            </Button>
          )}
        </Space>
      }
    >
      {query.length >= 2 && (
        <Card title={t('documents.results', { query })} loading={search.isPending} data-testid="search-results">
          {search.error ? <ProblemAlert error={search.error} /> : null}
          <List
            dataSource={search.data?.hits ?? []}
            locale={{ emptyText: t('documents.noResults') }}
            renderItem={(hit) => (
              <List.Item onClick={() => setSelected(hit.document_id)} style={{ cursor: 'pointer' }} data-testid="search-hit">
                <List.Item.Meta
                  title={
                    <Space wrap>
                      <Typography.Text strong>{hit.title}</Typography.Text>
                      {hit.heading_path.map((h) => (
                        <Tag key={h}>{h}</Tag>
                      ))}
                      {hit.page_start ? <Tag color="blue">{t('documents.page', { page: hit.page_start })}</Tag> : null}
                    </Space>
                  }
                  description={hit.snippet}
                />
              </List.Item>
            )}
          />
        </Card>
      )}
      <Card
        title={t('documents.library')}
        extra={
          <Space>
            <Select allowClear placeholder={t('documents.category')} style={{ minWidth: 200 }} value={category} onChange={setCategory} options={CATEGORIES.map((c) => ({ value: c, label: t(`documents.categories.${c}`) }))} />
            <Select allowClear placeholder={t('common.status')} style={{ minWidth: 180 }} value={status} onChange={setStatus} options={VERSION_STATUSES.map((s) => ({ value: s, label: t(`status.${s}`) }))} />
          </Space>
        }
      >
        {documents.error ? <ProblemAlert error={documents.error} /> : null}
        <Table<DocumentOut>
          rowKey="id"
          loading={documents.isPending}
          dataSource={documents.data?.items ?? []}
          locale={{ emptyText: t('documents.empty') }}
          onRow={(row) => ({ onClick: () => setSelected(row.id), style: { cursor: 'pointer' } })}
          data-testid="documents-table"
          columns={[
            { title: t('documents.docTitle'), dataIndex: 'title' },
            { title: t('documents.category'), dataIndex: 'category', render: (v: string) => t(`documents.categories.${v}`) },
            { title: t('common.status'), render: (_, row) => { const v = currentVersion(row); return v ? <StatusTag status={v.status} /> : '—' } },
            { title: 'v', render: (_, row) => currentVersion(row)?.version_no ?? '—' },
            { title: t('common.locale'), render: (_, row) => currentVersion(row)?.language ?? '—' },
            { title: t('common.updated'), dataIndex: 'updated_at', render: (v: string) => formatDateTime(v, i18n.language) },
          ]}
        />
      </Card>
      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)} />
      <DocumentDetail id={selected} onClose={() => setSelected(null)} />
    </PageHeader>
  )
}
