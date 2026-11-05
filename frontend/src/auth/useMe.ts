import { useQuery } from '@tanstack/react-query'
import { apiGet, type MeResponse } from '../api/client'
import { useOptionalAuth } from './useOptionalAuth'

// Keyed and enabled by the access token itself, so a session teardown never leaves a request without a token.
export function useMe() {
  const auth = useOptionalAuth()
  const token = auth?.isAuthenticated ? auth.user?.access_token : undefined
  return useQuery({
    queryKey: ['me', token],
    queryFn: () => apiGet<MeResponse>('/me', { headers: { Authorization: `Bearer ${token}` } }),
    enabled: Boolean(token),
    retry: false,
  })
}
