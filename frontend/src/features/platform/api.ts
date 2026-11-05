import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type TenantOut = Schemas['TenantOut']

export function useTenants() {
  return useQuery({ queryKey: ['platform', 'tenants'], queryFn: async () => unwrap(await api.GET('/api/v1/platform/tenants', { params: { query: { limit: 200 } } })) })
}
function useInvalidate() {
  const client = useQueryClient()
  return () => void client.invalidateQueries({ queryKey: ['platform'] })
}
export function useCreateTenant() {
  const invalidate = useInvalidate()
  return useApiMutation({ mutationFn: async (body: Schemas['TenantCreate']) => unwrap(await api.POST('/api/v1/platform/tenants', { body })), onSuccess: invalidate })
}
export function useSetTenantStatus() {
  const invalidate = useInvalidate()
  return useApiMutation({
    mutationFn: async ({ id, action }: { id: string; action: 'suspend' | 'resume' }) => {
      const path = action === 'suspend' ? '/api/v1/platform/tenants/{tenant_id}/suspend' : '/api/v1/platform/tenants/{tenant_id}/resume'
      return unwrap(await api.POST(path, { params: { path: { tenant_id: id } } }))
    },
    onSuccess: invalidate,
  })
}
export function useBootstrapAdmin() {
  const invalidate = useInvalidate()
  return useApiMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas['BootstrapAdminRequest'] }) =>
      unwrap(await api.POST('/api/v1/platform/tenants/{tenant_id}/bootstrap-admin', { params: { path: { tenant_id: id } }, body })),
    onSuccess: invalidate,
  })
}
export function usePlatformAudit() {
  return useQuery({ queryKey: ['platform', 'audit'], queryFn: async () => unwrap(await api.GET('/api/v1/platform/audit', { params: { query: { limit: 200 } } })) })
}
