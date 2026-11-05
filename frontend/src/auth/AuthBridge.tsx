import { useEffect } from 'react'
import { setUnauthorizedHandler } from '../api/client'
import { setAccessTokenGetter } from './token'
import { useOptionalAuth } from './useOptionalAuth'

// Publishes the current access token to the API client and redirects to sign-in when the API answers 401.
export function AuthBridge() {
  const auth = useOptionalAuth()
  useEffect(() => {
    setAccessTokenGetter(() => (auth?.isAuthenticated ? auth.user?.access_token : undefined))
  }, [auth])
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (auth && !auth.isLoading) void auth.signinRedirect()
    })
    return () => setUnauthorizedHandler(null)
  }, [auth])
  useEffect(() => () => setAccessTokenGetter(null), [])
  return null
}
