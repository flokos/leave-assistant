import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type ReviewItemOut = Schemas['ReviewItemOut']
export type RiskFindingOut = Schemas['RiskFindingOut']
export type ReviewAction = 'release' | 'reject' | 'delete' | 'inspect'

export function useReviewQueue() {
  return useQuery({
    queryKey: ['documents', 'review-queue'],
    queryFn: async () => unwrap(await api.GET('/api/v1/documents/review-queue')),
    refetchInterval: 30_000,
  })
}

export function useReviewDecision() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async ({ documentId, versionNo, action, comment }: { documentId: string; versionNo: number; action: ReviewAction; comment?: string }) =>
      unwrap(
        await api.POST('/api/v1/documents/{document_id}/versions/{version_no}/review', {
          params: { path: { document_id: documentId, version_no: versionNo } },
          body: { action, comment: comment?.trim() || null },
        }),
      ),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['documents'] }),
    silent: true,
  })
}
