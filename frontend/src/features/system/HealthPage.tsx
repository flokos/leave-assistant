import { useQuery } from '@tanstack/react-query'
import { Alert, Card, Descriptions, List, Space, Tag, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { getHealthz, getReadyz } from '../../api/client'

export function HealthPage() {
  const { t } = useTranslation()
  const healthz = useQuery({ queryKey: ['healthz'], queryFn: getHealthz, refetchInterval: 15_000 })
  const readyz = useQuery({ queryKey: ['readyz'], queryFn: getReadyz, refetchInterval: 15_000 })

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={3}>{t('system.title')}</Typography.Title>
      <Card title={t('system.liveness')}>
        {healthz.isPending && <Typography.Text>{t('system.loading')}</Typography.Text>}
        {healthz.isError && <Alert type="error" showIcon message={t('system.unreachable')} />}
        {healthz.data && (
          <Descriptions column={1} size="small">
            <Descriptions.Item label={t('system.version')}>{healthz.data.version}</Descriptions.Item>
            <Descriptions.Item label={t('system.environment')}>{healthz.data.app_env}</Descriptions.Item>
          </Descriptions>
        )}
      </Card>
      <Card title={t('system.readiness')}>
        {readyz.isPending && <Typography.Text>{t('system.loading')}</Typography.Text>}
        {readyz.isError && <Alert type="error" showIcon message={t('system.unreachable')} />}
        {readyz.data && (
          <List
            dataSource={readyz.data.checks}
            renderItem={(check) => (
              <List.Item data-testid={`check-${check.name}`}>
                <Space>
                  <Tag color={check.ok ? 'green' : 'red'}>{check.ok ? t('system.ok') : t('system.failed')}</Tag>
                  <Typography.Text strong>{check.name}</Typography.Text>
                  {!check.ok && <Typography.Text type="secondary">{check.detail}</Typography.Text>}
                </Space>
              </List.Item>
            )}
          />
        )}
      </Card>
    </Space>
  )
}
