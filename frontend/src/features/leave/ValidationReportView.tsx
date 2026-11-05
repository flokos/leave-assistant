import { Alert, Descriptions, Tag, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { formatDate } from '../../shared/dates'
import { formatDays } from '../../shared/days'
import type { ValidationReportOut } from './api'

/** Renders the backend's ValidationReport verbatim — the frontend evaluates no leave rule (FR-UI-04). */
export function ValidationReportView({ report }: { report: ValidationReportOut }) {
  const { t, i18n } = useTranslation()
  return (
    <div data-testid="validation-report">
      {!report.valid && (
        <Alert
          type="error"
          showIcon
          message={t('leave.violations')}
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {report.violations.map((v, i) => (
                <li key={i} data-testid={`violation-${v.code}`}>
                  {t(`problems.${v.code}`, { defaultValue: v.code })}
                  {v.detail ? ` — ${v.detail}` : ''}
                </li>
              ))}
            </ul>
          }
          style={{ marginBottom: 16 }}
        />
      )}
      {report.warnings.length > 0 && (
        <Alert type="warning" showIcon message={t('leave.warnings')} description={report.warnings.map((w) => t(`problems.${w.code}`, { defaultValue: w.code })).join('; ')} style={{ marginBottom: 16 }} />
      )}
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label={t('leave.workingDays')}>{report.working_days}</Descriptions.Item>
        <Descriptions.Item label={t('leave.excludedDates')}>
          {report.excluded_dates.length === 0
            ? t('common.none')
            : report.excluded_dates.map((e) => (
                <Tag key={e.date}>
                  {formatDate(e.date, i18n.language)} · {e.reason === 'holiday' ? (e.name ?? t('leave.holiday')) : t('leave.weekend')}
                </Tag>
              ))}
        </Descriptions.Item>
        <Descriptions.Item label={t('leave.balanceAfter')}>
          {report.balance ? (
            <Typography.Text type={report.balance.after_days < 0 ? 'danger' : undefined}>
              {formatDays(report.balance.after_days)} ({t('leave.available')}: {formatDays(report.balance.available_days)})
            </Typography.Text>
          ) : (
            t('leave.noBalance')
          )}
        </Descriptions.Item>
        <Descriptions.Item label={t('leave.approvalChain')}>
          {report.auto_acknowledged ? t('leave.autoAcknowledged') : report.approval_chain.map((r) => t(`stepRoles.${r}`)).join(' → ') || t('common.none')}
        </Descriptions.Item>
      </Descriptions>
    </div>
  )
}
