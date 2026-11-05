import { Tag } from 'antd'
import { useTranslation } from 'react-i18next'

const COLORS: Record<string, string> = {
  DRAFT: 'default',
  SUBMITTED: 'processing',
  PENDING_MANAGER: 'gold',
  PENDING_HR: 'orange',
  APPROVED: 'green',
  ACTIVE: 'cyan',
  COMPLETED: 'blue',
  REJECTED: 'red',
  CANCELLED: 'default',
  WITHDRAWN: 'default',
  MODIFICATION_PENDING: 'purple',
  pending: 'gold',
  approved: 'green',
  rejected: 'red',
  skipped: 'default',
  cancelled: 'default',
  active: 'green',
  inactive: 'default',
  suspended: 'red',
  UPLOADED: 'processing',
  PROCESSING: 'processing',
  FAILED: 'red',
  PENDING_REVIEW: 'orange',
  QUARANTINED: 'red',
  SUPERSEDED: 'default',
  RETIRED: 'default',
  DELETED: 'default',
}

export function StatusTag({ status }: { status: string }) {
  const { t } = useTranslation()
  return (
    <Tag color={COLORS[status] ?? 'default'} data-testid={`status-${status}`}>
      {t(`status.${status}`, status)}
    </Tag>
  )
}
