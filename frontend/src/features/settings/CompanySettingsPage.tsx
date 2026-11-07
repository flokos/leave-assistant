import { App, Button, Card, Descriptions, Form } from 'antd'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { useCompanySettings, useUpdateCompanySettings } from './api'
import { CompanySettingsFields } from './fields'

/** Company administrator (brief §11 "company policies, calendar configuration"): locale, timezone, work week, … */
export default function CompanySettingsPage() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const current = useCompanySettings()
  const save = useUpdateCompanySettings()
  const [form] = Form.useForm()
  useEffect(() => {
    if (current.data) form.setFieldsValue({ default_locale: current.data.default_locale, timezone: current.data.timezone, ...current.data.settings })
  }, [current.data, form])
  const submit = async () => {
    const v = await form.validateFields()
    await save.mutateAsync({
      default_locale: v.default_locale,
      timezone: v.timezone,
      settings: { work_week: [...v.work_week].sort(), conversation_retention_days: v.conversation_retention_days, proposal_ttl_minutes: v.proposal_ttl_minutes, allow_hr_fallback: v.allow_hr_fallback },
    })
    void message.success(t('settings.saved'))
  }
  return (
    <PageHeader title={t('settings.companyTitle')}>
      {current.error ? <ProblemAlert error={current.error} /> : null}
      {current.data && (
        <Descriptions size="small" column={2} items={[{ key: 'name', label: t('common.name'), children: current.data.name }, { key: 'slug', label: t('platform.slug'), children: current.data.slug }]} />
      )}
      <Card>
        <Form form={form} layout="vertical" disabled={current.isPending} data-testid="company-settings-form">
          <CompanySettingsFields />
          {save.error ? <ProblemAlert error={save.error} /> : null}
          <Button type="primary" onClick={() => void submit().catch(() => undefined)} loading={save.isPending} data-testid="settings-save">
            {t('common.save')}
          </Button>
        </Form>
      </Card>
    </PageHeader>
  )
}
