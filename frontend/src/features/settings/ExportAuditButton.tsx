import { DownloadOutlined } from '@ant-design/icons'
import { App, Button } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { describeError } from '../../shared/errors'
import { saveBlob } from '../../shared/download'
import { fetchAuditCsv } from './api'

/** Downloads the filtered audit log as CSV (M10); the backend audits the export and caps it at 10,000 rows. */
export function ExportAuditButton({ scope, filters }: { scope: 'tenant' | 'platform'; filters: { action?: string; from?: string; to?: string } }) {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      saveBlob(await fetchAuditCsv(scope, filters), `audit-${scope}-${new Date().toISOString().slice(0, 10)}.csv`)
    } catch (error) {
      void message.error(describeError(error, t))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Button icon={<DownloadOutlined />} onClick={() => void run()} loading={busy} data-testid="audit-export">
      {t('settings.exportCsv')}
    </Button>
  )
}
