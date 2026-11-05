import { Button, Result } from 'antd'
import { useTranslation } from 'react-i18next'
import { useOptionalAuth } from './useOptionalAuth'

export function SignInPage() {
  const { t } = useTranslation()
  const auth = useOptionalAuth()
  return (
    <Result
      status="info"
      title={t('auth.signInTitle')}
      subTitle={t('auth.signInHint')}
      extra={
        <Button type="primary" size="large" onClick={() => void auth?.signinRedirect()} data-testid="sign-in">
          {t('auth.signIn')}
        </Button>
      }
    />
  )
}
