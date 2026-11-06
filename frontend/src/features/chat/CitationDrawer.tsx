import { Descriptions, Drawer, Tag, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { useCitation } from './api'

/** The cited chunk as the reader may see it (`/documents/citations/{chunk_id}` re-checks permissions). */
export function CitationDrawer({ chunkId, onClose }: { chunkId: string | null; onClose: () => void }) {
  const { t } = useTranslation()
  const citation = useCitation(chunkId)
  const c = citation.data
  return (
    <Drawer open={Boolean(chunkId)} onClose={onClose} width={560} title={c?.title ?? t('chat.source')}>
      <div data-testid="citation-drawer">
        {citation.error ? <ProblemAlert error={citation.error} /> : null}
        {c && (
          <>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label={t('chat.section')}>{c.heading_path.slice(1).join(' › ') || '—'}</Descriptions.Item>
              <Descriptions.Item label={t('documents.page', { page: c.page_start ?? '—' })}>v{c.version_no}</Descriptions.Item>
            </Descriptions>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginTop: 16 }}>{c.content}</Typography.Paragraph>
            <Tag>{t(`documents.categories.${c.category}`)}</Tag>
          </>
        )}
      </div>
    </Drawer>
  )
}
