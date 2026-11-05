import { useTranslation } from 'react-i18next'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { AuditTable } from '../admin/AuditPage'
import { usePlatformAudit } from './api'

export default function PlatformAuditPage() {
  const { t } = useTranslation()
  const audit = usePlatformAudit()
  return (
    <PageHeader title={t('platform.audit')}>
      {audit.error ? <ProblemAlert error={audit.error} /> : null}
      <AuditTable entries={audit.data?.items ?? []} loading={audit.isPending} />
    </PageHeader>
  )
}
