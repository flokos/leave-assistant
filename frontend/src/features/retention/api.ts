import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type RetentionPolicyOut = Schemas['RetentionPolicyOut']
export type RetentionPolicyCreate = Schemas['RetentionPolicyCreate']
export type RetentionPolicyUpdate = Schemas['RetentionPolicyUpdate']
export type RetentionItemOut = Schemas['RetentionItemOut']
export type RetentionDecision = 'approved_delete' | 'retained'

export const DOCUMENT_CLASSES = ['supporting_document', 'company_document', 'conversation'] as const
export const RETENTION_ACTIONS = ['notify_only', 'delete_after_review'] as const

export function useRetentionPolicies() {
  return useQuery({ queryKey: ['retention', 'policies'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/retention-policies')) })
}

export function useRetentionItems(status: 'pending' | 'approved_delete' | 'retained' | 'deleted') {
  return useQuery({
    queryKey: ['retention', 'items', status],
    queryFn: async () => unwrap(await api.GET('/api/v1/admin/retention/review-items', { params: { query: { status } } })),
  })
}

function useInvalidateRetention() {
  const client = useQueryClient()
  return () => void client.invalidateQueries({ queryKey: ['retention'] })
}

export function useCreateRetentionPolicy() {
  const invalidate = useInvalidateRetention()
  return useApiMutation({
    mutationFn: async (body: RetentionPolicyCreate) => unwrap(await api.POST('/api/v1/admin/retention-policies', { body })),
    onSuccess: invalidate,
    silent: true,
  })
}

export function useUpdateRetentionPolicy() {
  const invalidate = useInvalidateRetention()
  return useApiMutation({
    mutationFn: async ({ policyId, body }: { policyId: string; body: RetentionPolicyUpdate }) =>
      unwrap(await api.PATCH('/api/v1/admin/retention-policies/{policy_id}', { params: { path: { policy_id: policyId } }, body })),
    onSuccess: invalidate,
  })
}

export function useDeleteRetentionPolicy() {
  const invalidate = useInvalidateRetention()
  return useApiMutation({
    mutationFn: async (policyId: string) =>
      unwrap(await api.DELETE('/api/v1/admin/retention-policies/{policy_id}', { params: { path: { policy_id: policyId } } })),
    onSuccess: invalidate,
  })
}

export function useDecideRetentionItem() {
  const invalidate = useInvalidateRetention()
  return useApiMutation({
    mutationFn: async ({ itemId, decision, comment }: { itemId: string; decision: RetentionDecision; comment?: string }) =>
      unwrap(
        await api.POST('/api/v1/admin/retention/review-items/{item_id}/decide', {
          params: { path: { item_id: itemId } },
          body: { decision, comment: comment?.trim() || null },
        }),
      ),
    onSuccess: invalidate,
    silent: true,
  })
}
