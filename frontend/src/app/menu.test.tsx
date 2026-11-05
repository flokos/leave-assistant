import { describe, expect, it } from 'vitest'
import type { Principal, Role } from '../auth/usePrincipal'
import { employeeMe } from '../test-utils'
import { menuItemsFor } from './menu'

function principalWith(roles: Role[], { platform = false } = {}): Principal {
  return {
    me: { ...employeeMe, roles, is_platform_admin: platform, tenant: platform ? null : employeeMe.tenant, employee: platform ? null : employeeMe.employee } as Principal['me'],
    roles,
    hasRole: (...wanted) => wanted.some((r) => roles.includes(r)),
    isPlatformAdmin: platform,
    isTenantUser: !platform,
    isLoading: false,
    isAuthenticated: true,
  }
}

function keysOf(principal: Principal): string[] {
  return menuItemsFor(principal, (k) => k).flatMap((group) => ('children' in group! && group.children ? group.children.map((c) => String(c!.key)) : []))
}

describe('workspace menu per role (UX only — the API re-authorises every call)', () => {
  it('employee: my leave, notifications, documents, system', () => {
    expect(keysOf(principalWith(['employee']))).toEqual(['/leave', '/notifications', '/documents', '/system'])
  })
  it('manager: adds approvals and team pages', () => {
    expect(keysOf(principalWith(['employee', 'manager']))).toEqual(['/leave', '/notifications', '/documents', '/approvals', '/team/availability', '/team/requests', '/system'])
  })
  it('hr admin: approvals, team, balances and audit but no company administration', () => {
    const keys = keysOf(principalWith(['employee', 'hr_admin']))
    expect(keys).toContain('/approvals')
    expect(keys).toContain('/admin/balances')
    expect(keys).toContain('/admin/review-queue')
    expect(keys).toContain('/admin/retention')
    expect(keys).toContain('/admin/audit')
    expect(keys).not.toContain('/admin/employees')
    expect(keys).not.toContain('/platform/tenants')
  })
  it('company admin: administration pages, balances and audit but no approvals', () => {
    const keys = keysOf(principalWith(['employee', 'company_admin']))
    expect(keys).toEqual(['/leave', '/notifications', '/documents', '/admin/employees', '/admin/departments', '/admin/leave-catalog', '/admin/holidays', '/admin/approval-rules', '/admin/balances', '/admin/review-queue', '/admin/retention', '/admin/audit', '/system'])
  })
  it('platform admin: tenants and platform audit only', () => {
    expect(keysOf(principalWith(['platform_admin'], { platform: true }))).toEqual(['/platform/tenants', '/platform/audit', '/system'])
  })
})
