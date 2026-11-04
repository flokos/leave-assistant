import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ConfigProvider } from 'antd'
import elGR from 'antd/locale/el_GR'
import enUS from 'antd/locale/en_US'
import { type ReactNode, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { AuthProvider } from 'react-oidc-context'

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000, retry: 1 } } })

// Keycloak login is enabled from M1 (realm export); the provider is wired behind a flag so the shell works without it.
const authEnabled = import.meta.env.VITE_AUTH_ENABLED === 'true'
const oidcConfig = {
  authority: `${import.meta.env.VITE_KEYCLOAK_URL ?? 'http://localhost:8081'}/realms/${import.meta.env.VITE_KEYCLOAK_REALM ?? 'leave-assistant'}`,
  client_id: import.meta.env.VITE_KEYCLOAK_CLIENT_ID ?? 'frontend-spa',
  redirect_uri: typeof window !== 'undefined' ? window.location.origin : '',
  automaticSilentRenew: true,
}

export function AppProviders({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation()
  const antdLocale = useMemo(() => (i18n.language === 'el' ? elGR : enUS), [i18n.language])
  const tree = (
    <ConfigProvider locale={antdLocale}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ConfigProvider>
  )
  return authEnabled ? <AuthProvider {...oidcConfig}>{tree}</AuthProvider> : tree
}
