// M10 settings and security pages: company settings and security policy (company administrator), platform settings
// and security defaults (platform administrator). Validation of ranges happens in the backend (422 → inline errors).
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type SettingsUpdate = Schemas['SettingsUpdate']
export type CompanySettingsOut = Schemas['CompanySettingsOut']
export type PlatformSettingsOut = Schemas['PlatformSettingsOut']
export type SecurityPolicyOut = Schemas['SecurityPolicyOut']
export type SecurityPolicyUpdate = Schemas['SecurityPolicyUpdate']
export type SecurityDefaultsOut = Schemas['SecurityDefaultsOut']
export type SecurityDefaultsUpdate = Schemas['SecurityDefaultsUpdate']

function useInvalidate(key: string[]) {
  const client = useQueryClient()
  return () => void client.invalidateQueries({ queryKey: key })
}

export function useCompanySettings() {
  return useQuery({ queryKey: ['settings', 'company'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/settings')) })
}
export function useUpdateCompanySettings() {
  const invalidate = useInvalidate(['settings', 'company'])
  return useApiMutation({ mutationFn: async (body: SettingsUpdate) => unwrap(await api.PUT('/api/v1/admin/settings', { body })), onSuccess: invalidate, silent: true })
}
export function usePlatformSettings() {
  return useQuery({ queryKey: ['settings', 'platform'], queryFn: async () => unwrap(await api.GET('/api/v1/platform/settings')) })
}
export function useUpdatePlatformSettings() {
  const invalidate = useInvalidate(['settings', 'platform'])
  return useApiMutation({ mutationFn: async (body: SettingsUpdate) => unwrap(await api.PUT('/api/v1/platform/settings', { body })), onSuccess: invalidate, silent: true })
}
export function useSecurityPolicy() {
  return useQuery({ queryKey: ['settings', 'security'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/security-policy')) })
}
export function useUpdateSecurityPolicy() {
  const invalidate = useInvalidate(['settings', 'security'])
  return useApiMutation({ mutationFn: async (body: SecurityPolicyUpdate) => unwrap(await api.PUT('/api/v1/admin/security-policy', { body })), onSuccess: invalidate, silent: true })
}
export function useSecurityDefaults() {
  return useQuery({ queryKey: ['settings', 'security-defaults'], queryFn: async () => unwrap(await api.GET('/api/v1/platform/security-defaults')) })
}
export function useUpdateSecurityDefaults() {
  const invalidate = useInvalidate(['settings', 'security-defaults'])
  return useApiMutation({ mutationFn: async (body: SecurityDefaultsUpdate) => unwrap(await api.PUT('/api/v1/platform/security-defaults', { body })), onSuccess: invalidate, silent: true })
}

/** GET a CSV export as a Blob (the backend sets the file name; the client keeps its own). */
export async function fetchAuditCsv(scope: 'tenant' | 'platform', query: { action?: string; from?: string; to?: string }): Promise<Blob> {
  const params = { query: { action: query.action || undefined, from: query.from || undefined, to: query.to || undefined } }
  const result = scope === 'tenant'
    ? await api.GET('/api/v1/admin/audit/export', { params, parseAs: 'blob' })
    : await api.GET('/api/v1/platform/audit/export', { params, parseAs: 'blob' })
  return unwrap(result) as Blob
}
