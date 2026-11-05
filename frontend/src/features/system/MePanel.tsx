import { Alert, Card, Descriptions, Tag, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../../api/client'
import { authEnabled } from '../../auth/token'
import { useMe } from '../../auth/useMe'
import { useOptionalAuth } from '../../auth/useOptionalAuth'

// Shows what the backend derived from the token: tenant, employee record, roles (doc 09 §2.1).
export function MePanel() {
  const { t } = useTranslation()
  const auth = useOptionalAuth()
  const me = useMe()
  if (!authEnabled || !auth) return null
  if (!auth.isAuthenticated) {
    return (
      <Card title={t('me.title')} data-testid="me-panel">
        <Typography.Text type="secondary">{t('me.signedOut')}</Typography.Text>
      </Card>
    )
  }
  return (
    <Card title={t('me.title')} data-testid="me-panel">
      {me.isPending && <Typography.Text>{t('system.loading')}</Typography.Text>}
      {me.isError && (
        <Alert
          type="error"
          showIcon
          message={
            me.error instanceof ApiError
              ? `${me.error.status} ${me.error.problem.title}`
              : t('system.unreachable')
          }
          description={me.error instanceof ApiError ? me.error.problem.detail : undefined}
        />
      )}
      {me.data && (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label={t('me.user')}>
            {me.data.display_name} ({me.data.username})
          </Descriptions.Item>
          <Descriptions.Item label={t('me.roles')}>
            {me.data.roles.map((role) => (
              <Tag key={role} color="blue">
                {t(`roles.${role}`, role)}
              </Tag>
            ))}
          </Descriptions.Item>
          <Descriptions.Item label={t('me.tenant')}>
            {me.data.tenant
              ? `${me.data.tenant.name} (${me.data.tenant.slug}, ${me.data.tenant.timezone})`
              : t('me.noTenant')}
          </Descriptions.Item>
          <Descriptions.Item label={t('me.employee')}>
            {me.data.employee
              ? `${me.data.employee.employee_number} · ${me.data.employee.department?.name ?? '—'}`
              : '—'}
          </Descriptions.Item>
          <Descriptions.Item label={t('me.locale')}>{me.data.locale}</Descriptions.Item>
        </Descriptions>
      )}
    </Card>
  )
}
