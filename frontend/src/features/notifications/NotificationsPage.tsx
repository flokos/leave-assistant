import { Button, Empty, List, Space, Switch, Typography } from 'antd'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDateTime } from '../../shared/dates'
import { PageHeader } from '../../shared/PageHeader'
import { useMarkAllRead, useMarkRead, useNotifications } from './api'
import { notificationText } from './text'

export default function NotificationsPage() {
  const { t, i18n } = useTranslation()
  const [unreadOnly, setUnreadOnly] = useState(true)
  const notifications = useNotifications(unreadOnly)
  const markRead = useMarkRead()
  const markAll = useMarkAllRead()
  return (
    <PageHeader
      title={t('notifications.title')}
      extra={
        <Space>
          <Switch checked={unreadOnly} onChange={setUnreadOnly} checkedChildren={t('notifications.unreadOnly')} unCheckedChildren={t('common.all')} />
          <Button onClick={() => markAll.mutate()} loading={markAll.isPending} data-testid="mark-all-read">
            {t('notifications.markAllRead')}
          </Button>
        </Space>
      }
    >
      <List
        loading={notifications.isPending}
        dataSource={notifications.data?.items ?? []}
        locale={{ emptyText: <Empty description={t('notifications.none')} /> }}
        renderItem={(n) => (
          <List.Item
            data-testid="notification"
            actions={n.read_at ? [] : [<Button key="read" size="small" onClick={() => markRead.mutate(n.id)}>{t('notifications.markRead')}</Button>]}
          >
            <List.Item.Meta
              title={<Typography.Text strong={!n.read_at}>{notificationText(n, t)}</Typography.Text>}
              description={formatDateTime(n.created_at, i18n.language)}
            />
          </List.Item>
        )}
      />
    </PageHeader>
  )
}
