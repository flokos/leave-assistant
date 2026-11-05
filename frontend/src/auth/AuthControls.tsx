import { Button, Space, Tag, Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import { authEnabled } from './token'
import { useMe } from './useMe'
import { useOptionalAuth } from './useOptionalAuth'

// Header: sign in / sign out and the signed-in identity. Role-based navigation arrives in M3.
export function AuthControls() {
  const { t } = useTranslation()
  const auth = useOptionalAuth()
  const me = useMe()
  if (!authEnabled || !auth) return null
  if (auth.isLoading) return <Typography.Text style={{ color: 'white' }}>{t('auth.loading')}</Typography.Text>
  if (!auth.isAuthenticated) {
    return (
      <Button type="primary" onClick={() => void auth.signinRedirect()}>
        {t('auth.signIn')}
      </Button>
    )
  }
  const name = me.data?.display_name || auth.user?.profile.preferred_username || ''
  return (
    <Space>
      <Typography.Text style={{ color: 'white' }}>{name}</Typography.Text>
      {me.data?.roles.map((role) => (
        <Tag key={role} color="blue">
          {t(`roles.${role}`, role)}
        </Tag>
      ))}
      <Button
        onClick={() => void auth.signoutRedirect({ post_logout_redirect_uri: window.location.origin + '/' })}
      >
        {t('auth.signOut')}
      </Button>
    </Space>
  )
}
