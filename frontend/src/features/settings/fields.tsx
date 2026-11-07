import { Checkbox, Form, Input, InputNumber, Select, Switch } from 'antd'
import { useTranslation } from 'react-i18next'

/** Monday-first weekday names in the UI language (ISO 0 = Monday, as the backend's work week). */
function weekdays(language: string): { value: number; label: string }[] {
  const monday = new Date(Date.UTC(2026, 10, 2)) // a Monday
  const format = new Intl.DateTimeFormat(language === 'el' ? 'el-GR' : 'en-GB', { weekday: 'short', timeZone: 'UTC' })
  return Array.from({ length: 7 }, (_, i) => ({ value: i, label: format.format(new Date(monday.getTime() + i * 86_400_000)) }))
}

/** Locale, timezone and the per-company settings shared by the company and the platform pages. */
export function CompanySettingsFields() {
  const { t, i18n } = useTranslation()
  return (
    <>
      <Form.Item name="default_locale" label={t('settings.defaultLocale')} rules={[{ required: true }]}>
        <Select data-testid="settings-locale" options={[{ value: 'el', label: 'Ελληνικά' }, { value: 'en', label: 'English' }]} style={{ width: 200 }} />
      </Form.Item>
      <Form.Item name="timezone" label={t('settings.timezone')} rules={[{ required: true }]} extra={t('settings.timezoneHint')}>
        <Input data-testid="settings-timezone" style={{ width: 260 }} />
      </Form.Item>
      <Form.Item name="work_week" label={t('settings.workWeek')} rules={[{ required: true }]}>
        <Checkbox.Group data-testid="settings-work-week" options={weekdays(i18n.language)} />
      </Form.Item>
      <Form.Item name="conversation_retention_days" label={t('settings.conversationRetentionDays')} extra={t('settings.conversationRetentionHint')}>
        <InputNumber data-testid="settings-retention" min={1} max={3650} />
      </Form.Item>
      <Form.Item name="proposal_ttl_minutes" label={t('settings.proposalTtlMinutes')} extra={t('settings.proposalTtlHint')}>
        <InputNumber data-testid="settings-proposal-ttl" min={1} max={1440} />
      </Form.Item>
      <Form.Item name="allow_hr_fallback" label={t('settings.allowHrFallback')} valuePropName="checked" extra={t('settings.allowHrFallbackHint')}>
        <Switch data-testid="settings-hr-fallback" />
      </Form.Item>
    </>
  )
}

/** Risk tiers, chat screening, upload limits: shared by the company policy and the platform defaults. */
export function SecurityFields({ mime = true }: { mime?: boolean }) {
  const { t } = useTranslation()
  const score = { min: 0, max: 1, step: 0.05 }
  return (
    <>
      <Form.Item name="risk_low_max" label={t('settings.riskLowMax')} extra={t('settings.riskLowMaxHint')} rules={[{ required: true }]}>
        <InputNumber data-testid="security-risk-low" {...score} />
      </Form.Item>
      <Form.Item name="risk_medium_max" label={t('settings.riskMediumMax')} extra={t('settings.riskMediumMaxHint')} rules={[{ required: true }]}>
        <InputNumber data-testid="security-risk-medium" {...score} />
      </Form.Item>
      <Form.Item name="user_input_block_threshold" label={t('settings.userInputBlock')} extra={t('settings.userInputBlockHint')} rules={[{ required: true }]}>
        <InputNumber data-testid="security-input-block" {...score} />
      </Form.Item>
      <Form.Item name="injection_llm_judge" label={t('settings.llmJudge')} valuePropName="checked" extra={t('settings.llmJudgeHint')}>
        <Switch data-testid="security-llm-judge" />
      </Form.Item>
      <Form.Item name="max_upload_mb" label={t('settings.maxUploadMb')} rules={[{ required: true }]}>
        <InputNumber data-testid="security-max-upload" min={1} max={500} />
      </Form.Item>
      {mime && (
        <Form.Item name="allowed_mime" label={t('settings.allowedMime')} extra={t('settings.allowedMimeHint')}>
          <Select data-testid="security-mime" mode="tags" style={{ width: '100%' }} />
        </Form.Item>
      )}
    </>
  )
}
