import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type ConnectionOut = Schemas['ConnectionOut']
export type SyncJobOut = Schemas['SyncJobOut']
export type ReconciliationOut = Schemas['ReconciliationOut']
export type ReconciliationItemOut = Schemas['ReconciliationItemOut']
export type ConnectionKind = 'company_shared' | 'personal'
export type ItemAction = 'relink' | 'ignore' | 'delete-orphan'
export type SyncStatus = 'pending' | 'running' | 'succeeded' | 'parked' | 'dead'

export function useConnections() {
  return useQuery({
    queryKey: ['calendar', 'connections'],
    queryFn: async () => unwrap(await api.GET('/api/v1/calendar/connections')),
  })
}

/** Starts the consent: the backend returns the provider's URL (the mock answers with the callback directly). */
export function useStartConnection() {
  return useApiMutation({
    mutationFn: async (kind: ConnectionKind) =>
      unwrap(await api.POST('/api/v1/calendar/connections/google/start', { body: { kind } })),
    onSuccess: (data) => window.location.assign(data.authorization_url),
  })
}

export function useRevokeConnection() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.DELETE('/api/v1/calendar/connections/{connection_id}', { params: { path: { connection_id: id } } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['calendar'] }),
  })
}

export function useSyncJobs(status: SyncStatus | undefined) {
  return useQuery({
    queryKey: ['calendar', 'sync-jobs', status],
    queryFn: async () => unwrap(await api.GET('/api/v1/calendar/sync-jobs', { params: { query: { status } } })),
    refetchInterval: 15_000,
  })
}

export function useRetrySyncJob() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.POST('/api/v1/calendar/sync-jobs/{job_id}/retry', { params: { path: { job_id: id } } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['calendar'] }),
  })
}

export function useReconciliation(enabled: boolean) {
  return useQuery({
    queryKey: ['calendar', 'reconciliation'],
    queryFn: async () => unwrap(await api.GET('/api/v1/calendar/reconciliation')),
    enabled,
  })
}

export function useRunReconciliation() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/calendar/reconcile')),
    onSuccess: (data) => {
      client.setQueryData(['calendar', 'reconciliation'], data)
      void client.invalidateQueries({ queryKey: ['calendar', 'sync-jobs'] })
    },
  })
}

export function useItemAction() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async ({ id, action }: { id: string; action: ItemAction }) =>
      unwrap(
        await api.POST('/api/v1/calendar/reconciliation/items/{item_id}/{action}', {
          params: { path: { item_id: id, action } },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['calendar'] }),
  })
}
