import { useEffect } from 'react'
import { setAccessTokenGetter } from './token'
import { useOptionalAuth } from './useOptionalAuth'

// Publishes the current access token to the plain fetch client (used by every API call except /me, which binds
// the token explicitly). Cleared only when the bridge unmounts.
export function AuthBridge() {
  const auth = useOptionalAuth()
  useEffect(() => {
    setAccessTokenGetter(() => (auth?.isAuthenticated ? auth.user?.access_token : undefined))
  }, [auth])
  useEffect(() => () => setAccessTokenGetter(null), [])
  return null
}
