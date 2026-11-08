import { Alert, App, Button, Card, Form } from 'antd'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { PageHeader } from '../../shared/PageHeader'
import { ProblemAlert } from '../../shared/ProblemAlert'
import { useSecurityPolicy, useUpdateSecurityPolicy } from './api'
import { SecurityFields } from './fields'

/** The company's content-security thresholds (doc 08 §8.2) within the ranges the platform allows (422 otherwise). */
export default function SecurityPolicyPage() {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const current = useSecurityPolicy()
  const save = useUpdateSecurityPolicy()
  const [form] = Form.useForm()
  useEffect(() => {
    if (current.data) form.setFieldsValue(current.data)
  }, [current.data, form])
  const submit = async () => {
    const v = await form.validateFields()
    await save.mutateAsync({
      risk_low_max: v.risk_low_max,
      risk_medium_max: v.risk_medium_max,
      user_input_block_threshold: v.user_input_block_threshold,
      injection_llm_judge: v.injection_llm_judge,
      max_upload_mb: v.max_upload_mb,
      allowed_mime: v.allowed_mime,
    })
    void message.success(t('settings.saved'))
  }
  return (
    <PageHeader title={t('settings.securityTitle')}>
      <Alert type="info" showIcon message={t('settings.securityIntro')} />
      {current.error ? <ProblemAlert error={current.error} /> : null}
      <Card>
        <Form form={form} layout="vertical" disabled={current.isPending} data-testid="security-policy-form">
          <SecurityFields />
          {save.error ? <ProblemAlert error={save.error} /> : null}
          <Button type="primary" onClick={() => void submit().catch(() => undefined)} loading={save.isPending} data-testid="security-save">
            {t('common.save')}
          </Button>
        </Form>
      </Card>
    </PageHeader>
  )
}
