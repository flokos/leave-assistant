import { Result, Spin } from 'antd'
import { useTranslation } from 'react-i18next'
import { Outlet } from 'react-router'
import { authEnabled } from '../auth/token'
import { SignInPage } from '../auth/SignInPage'
import { type Role, usePrincipal } from '../auth/usePrincipal'

/** UX-only gate (FR-UI-04): hides what the role cannot use; the API re-checks every call. */
export function RequireRole({ anyOf, tenantOnly }: { anyOf?: Role[]; tenantOnly?: boolean }) {
  const { t } = useTranslation()
  const principal = usePrincipal()
  if (!authEnabled) return <Outlet />
  if (!principal.isAuthenticated) return <SignInPage />
  if (principal.isLoading || !principal.me) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <Spin size="large" />
      </div>
    )
  }
  const allowed = (!tenantOnly || principal.isTenantUser) && (!anyOf || principal.hasRole(...anyOf))
  if (!allowed) {
    return <Result status="403" title={t('errors.forbiddenTitle')} subTitle={t('errors.forbiddenHint')} data-testid="forbidden" />
  }
  return <Outlet />
}
