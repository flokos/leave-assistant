import { BellOutlined, GlobalOutlined, LogoutOutlined, UserOutlined } from '@ant-design/icons'
import { Badge, Button, Dropdown, Layout, Menu, Segmented, Space, Typography, type MenuProps } from 'antd'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Outlet, useLocation, useNavigate } from 'react-router'
import { authEnabled } from '../auth/token'
import { useOptionalAuth } from '../auth/useOptionalAuth'
import { usePrincipal } from '../auth/usePrincipal'
import { useUnreadCount } from '../features/notifications/api'
import { type Locale, setLocale } from '../i18n'
import { menuItemsFor } from './menu'

function selectedKey(pathname: string): string {
  const candidates = ['/leave', '/notifications', '/documents', '/calendar', '/approvals', '/team/availability', '/team/requests', '/admin/employees', '/admin/departments', '/admin/leave-catalog', '/admin/holidays', '/admin/approval-rules', '/admin/balances', '/admin/audit', '/platform/tenants', '/platform/audit', '/system']
  return candidates.find((c) => pathname === c || pathname.startsWith(c + '/')) ?? pathname
}

export function AppShell() {
  const { t, i18n } = useTranslation()
  const auth = useOptionalAuth()
  const principal = usePrincipal()
  const location = useLocation()
  const navigate = useNavigate()
  const unread = useUnreadCount(principal.isTenantUser)
  const items = useMemo(() => menuItemsFor(principal, t), [principal, t])
  const userMenu: MenuProps['items'] = [
    { key: 'roles', label: (principal.me?.roles ?? []).map((r) => t(`roles.${r}`)).join(', '), disabled: true },
    { type: 'divider' },
    { key: 'signout', icon: <LogoutOutlined />, label: t('auth.signOut'), onClick: () => void auth?.signoutRedirect({ post_logout_redirect_uri: window.location.origin + '/' }) },
  ]
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
        <Typography.Title level={4} style={{ color: 'white', margin: 0 }}>
          <Link to="/" style={{ color: 'white' }}>
            {t('app.title')}
          </Link>
        </Typography.Title>
        <Space size="middle">
          {principal.isTenantUser && (
            <Badge count={unread} size="small" data-testid="notifications-badge">
              <Button type="text" icon={<BellOutlined style={{ color: 'white', fontSize: 18 }} />} onClick={() => navigate('/notifications')} aria-label={t('nav.notifications')} />
            </Badge>
          )}
          <Segmented
            aria-label={t('app.language')}
            value={i18n.language}
            options={[
              { label: 'EN', value: 'en' },
              { label: 'EL', value: 'el' },
            ]}
            onChange={(value) => setLocale(value as Locale)}
            data-testid="language-toggle"
          />
          {authEnabled && auth?.isAuthenticated ? (
            <Dropdown menu={{ items: userMenu }} trigger={['click', 'hover']}>
              <Button type="text" icon={<UserOutlined />} style={{ color: 'white' }} data-testid="user-menu">
                {principal.me?.display_name ?? auth.user?.profile.preferred_username}
              </Button>
            </Dropdown>
          ) : authEnabled ? (
            <Button type="primary" onClick={() => void auth?.signinRedirect()} data-testid="sign-in">
              {t('auth.signIn')}
            </Button>
          ) : null}
          <GlobalOutlined style={{ color: 'white' }} />
        </Space>
      </Layout.Header>
      <Layout>
        {principal.me && (
          <Layout.Sider width={240} breakpoint="lg" collapsedWidth={0} theme="light" data-testid="workspace-menu">
            <Menu mode="inline" selectedKeys={[selectedKey(location.pathname)]} items={items} style={{ height: '100%', borderRight: 0 }} />
          </Layout.Sider>
        )}
        <Layout.Content style={{ padding: 24 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
