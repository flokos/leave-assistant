import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type ConversationOut = Schemas['ConversationOut']
export type MessageOut = Schemas['MessageOut']
export type CitationItem = Schemas['CitationItem']

export function useConversations() {
  return useQuery({ queryKey: ['chat', 'conversations'], queryFn: async () => unwrap(await api.GET('/api/v1/chat/conversations', { params: { query: { limit: 50 } } })) })
}

export function useMessages(conversationId: string | null) {
  return useQuery({
    queryKey: ['chat', 'messages', conversationId],
    queryFn: async () => unwrap(await api.GET('/api/v1/chat/conversations/{conversation_id}/messages', { params: { path: { conversation_id: conversationId as string } } })),
    enabled: Boolean(conversationId),
  })
}

export function useCreateConversation() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/chat/conversations')),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['chat', 'conversations'] }),
  })
}

export function useArchiveConversation() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async (conversationId: string) =>
      unwrap(await api.DELETE('/api/v1/chat/conversations/{conversation_id}', { params: { path: { conversation_id: conversationId } } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['chat'] }),
  })
}

export function useCitation(chunkId: string | null) {
  return useQuery({
    queryKey: ['documents', 'citation', chunkId],
    queryFn: async () => unwrap(await api.GET('/api/v1/documents/citations/{chunk_id}', { params: { path: { chunk_id: chunkId as string } } })),
    enabled: Boolean(chunkId),
  })
}
