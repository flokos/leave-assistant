import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type NotificationOut = Schemas['NotificationOut']

export function useNotifications(unread: boolean, enabled = true) {
  return useQuery({
    queryKey: ['notifications', unread],
    queryFn: async () => unwrap(await api.GET('/api/v1/notifications', { params: { query: { unread, limit: 100 } } })),
    enabled,
    refetchInterval: 30_000,
  })
}

export function useUnreadCount(enabled: boolean): number {
  const query = useNotifications(true, enabled)
  return query.data?.items.length ?? 0
}

export function useMarkRead() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async (id: string) => unwrap(await api.POST('/api/v1/notifications/{notification_id}/read', { params: { path: { notification_id: id } } })),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['notifications'] }),
  })
}

export function useMarkAllRead() {
  const client = useQueryClient()
  return useApiMutation({
    mutationFn: async () => unwrap(await api.POST('/api/v1/notifications/read-all')),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['notifications'] }),
  })
}
