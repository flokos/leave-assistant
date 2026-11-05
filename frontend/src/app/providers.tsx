import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import elGR from 'antd/locale/el_GR'
import enUS from 'antd/locale/en_US'
import { type ReactNode, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { AuthProvider, type AuthProviderProps } from 'react-oidc-context'
import { AuthBridge } from '../auth/AuthBridge'
import { authEnabled } from '../auth/token'
import { ErrorBoundary } from './ErrorBoundary'

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, retry: 1 } } })

// Keycloak login (Authorization Code + PKCE, FR-AUTH-01). The OIDC session lives in sessionStorage (oidc-client-ts
// default; cleared when the tab closes; never localStorage — ADR-0024). Mounted only when VITE_AUTH_ENABLED=true.
const origin = typeof window !== 'undefined' ? window.location.origin : ''
const oidcConfig: AuthProviderProps = {
  authority: `${import.meta.env.VITE_KEYCLOAK_URL ?? 'http://localhost:8081'}/realms/${import.meta.env.VITE_KEYCLOAK_REALM ?? 'leave-assistant'}`,
  client_id: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? 'frontend-spa',
  redirect_uri: `${origin}/`,
  post_logout_redirect_uri: `${origin}/`,
  scope: 'openid profile email',
  automaticSilentRenew: true,
  onSigninCallback: () => {
    window.history.replaceState({}, document.title, window.location.pathname)
  },
}

export function AppProviders({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation()
  const antdLocale = useMemo(() => (i18n.language === 'el' ? elGR : enUS), [i18n.language])
  const tree = (
    <ConfigProvider locale={antdLocale}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          {authEnabled && <AuthBridge />}
          <ErrorBoundary>{children}</ErrorBoundary>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  )
  return authEnabled ? <AuthProvider {...oidcConfig}>{tree}</AuthProvider> : tree
}
