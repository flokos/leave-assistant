import type { MeResponse } from '../api/client'
import { useMe } from './useMe'
import { useOptionalAuth } from './useOptionalAuth'

export type Role = 'employee' | 'manager' | 'hr_admin' | 'company_admin' | 'platform_admin' | 'automation'

export interface Principal {
  me: MeResponse | undefined
  roles: Role[]
  hasRole: (...roles: Role[]) => boolean
  isPlatformAdmin: boolean
  isTenantUser: boolean
  isLoading: boolean
  isAuthenticated: boolean
}

/** Roles come from the backend's /me — navigation uses them for UX only; every call is re-authorised server-side. */
export function usePrincipal(): Principal {
  const auth = useOptionalAuth()
  const me = useMe()
  const roles = (me.data?.roles ?? []) as Role[]
  return {
    me: me.data,
    roles,
    hasRole: (...wanted) => wanted.some((r) => roles.includes(r)),
    isPlatformAdmin: Boolean(me.data?.is_platform_admin),
    isTenantUser: Boolean(me.data?.tenant),
    isLoading: Boolean(auth?.isLoading) || (Boolean(auth?.isAuthenticated) && me.isPending),
    isAuthenticated: Boolean(auth?.isAuthenticated),
  }
}
