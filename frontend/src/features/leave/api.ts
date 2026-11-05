import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type LeaveTypeOut = Schemas['LeaveTypeOut']
export type BalanceOut = Schemas['BalanceOut']
export type LeaveRequestOut = Schemas['LeaveRequestOut']
export type LeaveRequestSummary = Schemas['LeaveRequestSummary']
export type ValidationReportOut = Schemas['ValidationReportOut']
export type SubmitRequestIn = Schemas['SubmitRequestIn']

export function useLeaveTypes() {
  return useQuery({ queryKey: ['leave', 'types'], queryFn: async () => unwrap(await api.GET('/api/v1/leave/types')), staleTime: 60_000 })
}

/** Localised leave-type names from the catalogue, falling back to i18n keys for codes outside the catalogue. */
export function useLeaveTypeName() {
  const { t, i18n } = useTranslation()
  const types = useLeaveTypes()
  return (code: string | null | undefined): string => {
    if (!code) return t('team.absenceOnly')
    const found = types.data?.find((x) => x.code === code)
    if (found) return i18n.language === 'el' ? found.name_el : found.name_en
    return t(`leaveTypes.${code}`, { defaultValue: code })
  }
}

export function useBalances(year: number) {
  return useQuery({ queryKey: ['leave', 'balances', year], queryFn: async () => unwrap(await api.GET('/api/v1/leave/balances', { params: { query: { year } } })) })
}

export function useMyRequests(statuses: string[] | undefined) {
  return useQuery({
    queryKey: ['leave', 'requests', statuses ?? []],
    queryFn: async () =>
      unwrap(await api.GET('/api/v1/leave/requests', { params: { query: { status: statuses as never, limit: 200 } } })),
  })
}

export function useRequest(id: string | null) {
  return useQuery({
    queryKey: ['leave', 'request', id],
    queryFn: async () => unwrap(await api.GET('/api/v1/leave/requests/{request_id}', { params: { path: { request_id: id as string } } })),
    enabled: Boolean(id),
  })
}

export function useValidateRequest() {
  return useApiMutation({
    mutationFn: async (body: SubmitRequestIn) => unwrap(await api.POST('/api/v1/leave/requests/validate', { body })),
    silent: true,
  })
}

function useInvalidateLeave() {
  const client = useQueryClient()
  return () => {
    void client.invalidateQueries({ queryKey: ['leave'] })
    void client.invalidateQueries({ queryKey: ['notifications'] })
  }
}

export function useSubmitRequest() {
  const invalidate = useInvalidateLeave()
  return useApiMutation({
    mutationFn: async (body: SubmitRequestIn) => unwrap(await api.POST('/api/v1/leave/requests', { body })),
    onSuccess: invalidate,
    silent: true,
  })
}

export function useModifyRequest() {
  const invalidate = useInvalidateLeave()
  return useApiMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas['ModifyRequestIn'] }) =>
      unwrap(await api.POST('/api/v1/leave/requests/{request_id}/modify', { params: { path: { request_id: id } }, body })),
    onSuccess: invalidate,
  })
}

export function useCancelRequest() {
  const invalidate = useInvalidateLeave()
  return useApiMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas['CancelRequestIn'] }) =>
      unwrap(await api.POST('/api/v1/leave/requests/{request_id}/cancel', { params: { path: { request_id: id } }, body })),
    onSuccess: invalidate,
  })
}
