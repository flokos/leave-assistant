import { App, Button, Card, Form, InputNumber } from 'antd'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import {
  usePlatformSettings,
  useSecurityDefaults,
  useUpdatePlatformSettings,
  useUpdateSecurityDefaults,
} from './api'
import { CompanySettingsFields, SecurityFields } from './fields'

/** Platform administrator (brief §11 "platform configuration, security policy management"): the defaults of new
 * companies and the global security defaults with the ranges companies may use. */
export default function PlatformSettingsPage() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const settings = usePlatformSettings()
  const security = useSecurityDefaults()
  const saveSettings = useUpdatePlatformSettings()
  const saveSecurity = useUpdateSecurityDefaults()
  const [settingsForm] = Form.useForm()
  const [securityForm] = Form.useForm()
  useEffect(() => {
    if (settings.data) settingsForm.setFieldsValue({ default_locale: settings.data.default_locale, timezone: settings.data.timezone, ...settings.data.tenant_settings })
  }, [settings.data, settingsForm])
  useEffect(() => {
    if (security.data) securityForm.setFieldsValue(security.data)
  }, [security.data, securityForm])
  const submitSettings = async () => {
    const v = await settingsForm.validateFields()
    await saveSettings.mutateAsync({
      default_locale: v.default_locale,
      timezone: v.timezone,
      settings: { work_week: [...v.work_week].sort(), conversation_retention_days: v.conversation_retention_days, proposal_ttl_minutes: v.proposal_ttl_minutes, allow_hr_fallback: v.allow_hr_fallback },
    })
    void message.success(t('settings.saved'))
  }
  const submitSecurity = async () => {
    const v = await securityForm.validateFields()
    await saveSecurity.mutateAsync({
      risk_low_max: v.risk_low_max,
      risk_medium_max: v.risk_medium_max,
      user_input_block_threshold: v.user_input_block_threshold,
      injection_llm_judge: v.injection_llm_judge,
      max_upload_mb: v.max_upload_mb,
      threshold_min: v.threshold_min,
      threshold_max: v.threshold_max,
      max_upload_mb_limit: v.max_upload_mb_limit,
    })
    void message.success(t('settings.saved'))
  }
  return (
    <PageHeader title={t('settings.platformTitle')}>
      <Card title={t('settings.newCompanyDefaults')}>
        {settings.error ? <ProblemAlert error={settings.error} /> : null}
        <Form form={settingsForm} layout="vertical" disabled={settings.isPending} data-testid="platform-settings-form">
          <CompanySettingsFields />
          {saveSettings.error ? <ProblemAlert error={saveSettings.error} /> : null}
          <Button type="primary" onClick={() => void submitSettings().catch(() => undefined)} loading={saveSettings.isPending} data-testid="platform-settings-save">
            {t('common.save')}
          </Button>
        </Form>
      </Card>
      <Card title={t('settings.securityDefaults')}>
        {security.error ? <ProblemAlert error={security.error} /> : null}
        <Form form={securityForm} layout="vertical" disabled={security.isPending} data-testid="security-defaults-form">
          <SecurityFields mime={false} />
          <Form.Item name="threshold_min" label={t('settings.thresholdMin')} extra={t('settings.thresholdRangeHint')} rules={[{ required: true }]}>
            <InputNumber data-testid="security-threshold-min" min={0} max={1} step={0.05} />
          </Form.Item>
          <Form.Item name="threshold_max" label={t('settings.thresholdMax')} rules={[{ required: true }]}>
            <InputNumber data-testid="security-threshold-max" min={0} max={1} step={0.05} />
          </Form.Item>
          <Form.Item name="max_upload_mb_limit" label={t('settings.maxUploadLimit')} rules={[{ required: true }]}>
            <InputNumber data-testid="security-max-upload-limit" min={1} max={500} />
          </Form.Item>
          {saveSecurity.error ? <ProblemAlert error={saveSecurity.error} /> : null}
          <Button type="primary" onClick={() => void submitSecurity().catch(() => undefined)} loading={saveSecurity.isPending} data-testid="security-defaults-save">
            {t('common.save')}
          </Button>
        </Form>
      </Card>
    </PageHeader>
  )
}
