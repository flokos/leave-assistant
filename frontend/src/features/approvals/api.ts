import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type PendingItemOut = Schemas['PendingItemOut']

export function usePendingApprovals() {
  return useQuery({ queryKey: ['approvals', 'pending'], queryFn: async () => unwrap(await api.GET('/api/v1/approvals/pending')), refetchInterval: 30_000 })
}

export function useDecide() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async ({ stepId, decision, version, comment }: { stepId: string; decision: 'approve' | 'reject'; version: number; comment?: string }) => {
      const path = decision === 'approve' ? '/api/v1/approvals/{step_id}/approve' : '/api/v1/approvals/{step_id}/reject'
      return unwrap(await api.POST(path, { params: { path: { step_id: stepId } }, body: { version, comment: comment?.trim() || null } }))
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['approvals'] })
      void client.invalidateQueries({ queryKey: ['leave'] })
      void client.invalidateQueries({ queryKey: ['notifications'] })
    },
    silent: true,
  })
}
