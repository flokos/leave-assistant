import { useContext } from 'react'
import { AuthContext, type AuthContextProps } from 'react-oidc-context'

// `useAuth` throws without a provider; the provider is only mounted when VITE_AUTH_ENABLED=true.
export function useOptionalAuth(): AuthContextProps | undefined {
  return useContext(AuthContext)
}
