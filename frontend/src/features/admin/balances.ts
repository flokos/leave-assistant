import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '../../api/client'
import { useTeamEmployees } from '../team/api'
import { usePostAdjustment, type TenantBalanceOut, useTenantBalances } from './api'

export { usePostAdjustment, useTenantBalances }
export type { TenantBalanceOut }

/** Employee names for HR/company administrators: HR reads them from /team/employees (tenant-wide). */
export function useTeamBalanceNames() {
  const employees = useTeamEmployees()
  const lookup = (id: string) => {
    const e = employees.data?.find((x) => x.id === id)
    return e ? `${e.first_name} ${e.last_name} (${e.employee_number})` : id
  }
  return Object.assign(lookup, { options: (employees.data ?? []).map((e) => ({ value: e.id, label: `${e.first_name} ${e.last_name} (${e.employee_number})` })) })
}

export function useAuditActions() {
  return useQuery({ queryKey: ['admin', 'audit-actions'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/audit', { params: { query: { limit: 200 } } })) })
}
