import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'
import { useApiMutation } from '../../shared/useApiMutation'

export type EmployeeOut = Schemas['EmployeeOut']
export type DepartmentOut = Schemas['DepartmentOut']
export type PolicyOut = Schemas['PolicyOut']
export type HolidayOut = Schemas['HolidayOut']
export type RuleOut = Schemas['RuleOut']
export type AuditEntryOut = Schemas['AuditEntryOut']
export type TenantBalanceOut = Schemas['TenantBalanceOut']

function useInvalidate(prefix: string) {
  const client = useQueryClient()
  return () => void client.invalidateQueries({ queryKey: [prefix] })
}

// --- employees ------------------------------------------------------------------------------------------
export function useEmployees() {
  return useQuery({ queryKey: ['admin', 'employees'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/employees', { params: { query: { limit: 200 } } })) })
}
export function useCreateEmployee() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (body: Schemas['EmployeeCreate']) => unwrap(await api.POST('/api/v1/admin/employees', { body })), onSuccess: invalidate })
}
export function useUpdateEmployee() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas['EmployeeUpdate'] }) => unwrap(await api.PATCH('/api/v1/admin/employees/{employee_id}', { params: { path: { employee_id: id } }, body })),
    onSuccess: invalidate,
  })
}
export function useDeactivateEmployee() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (id: string) => unwrap(await api.POST('/api/v1/admin/employees/{employee_id}/deactivate', { params: { path: { employee_id: id } } })), onSuccess: invalidate })
}
export function useSetRoles() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({
    mutationFn: async ({ id, roles }: { id: string; roles: Schemas['EmployeeRolesUpdate']['roles'] }) => unwrap(await api.PUT('/api/v1/admin/employees/{employee_id}/roles', { params: { path: { employee_id: id } }, body: { roles } })),
    onSuccess: invalidate,
  })
}

// --- departments ----------------------------------------------------------------------------------------
export function useDepartments() {
  return useQuery({ queryKey: ['admin', 'departments'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/departments')) })
}
export function useCreateDepartment() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (body: Schemas['DepartmentCreate']) => unwrap(await api.POST('/api/v1/admin/departments', { body })), onSuccess: invalidate })
}
export function useUpdateDepartment() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas['DepartmentUpdate'] }) => unwrap(await api.PATCH('/api/v1/admin/departments/{department_id}', { params: { path: { department_id: id } }, body })),
    onSuccess: invalidate,
  })
}
export function useDeleteDepartment() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (id: string) => unwrap(await api.DELETE('/api/v1/admin/departments/{department_id}', { params: { path: { department_id: id } } })), onSuccess: invalidate })
}
export function useSetManager() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({
    mutationFn: async ({ id, employeeId }: { id: string; employeeId: string | null }) => unwrap(await api.PUT('/api/v1/admin/departments/{department_id}/manager', { params: { path: { department_id: id } }, body: { employee_id: employeeId } })),
    onSuccess: invalidate,
  })
}

// --- leave catalogue --------------------------------------------------------------------------------------
export function useAdminLeaveTypes() {
  return useQuery({ queryKey: ['admin', 'leave-types'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/leave-types', { params: { query: { include_inactive: true } } })) })
}
export function useCreateLeaveType() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (body: Schemas['LeaveTypeCreate']) => unwrap(await api.POST('/api/v1/admin/leave-types', { body })), onSuccess: invalidate })
}
export function useUpdateLeaveType() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas['LeaveTypeUpdate'] }) => unwrap(await api.PATCH('/api/v1/admin/leave-types/{leave_type_id}', { params: { path: { leave_type_id: id } }, body })),
    onSuccess: invalidate,
  })
}
export function usePolicies(year?: number) {
  return useQuery({ queryKey: ['admin', 'policies', year ?? 'all'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/leave-policies', { params: { query: year ? { year } : {} } })) })
}
export function useCreatePolicy() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (body: Schemas['PolicyCreate']) => unwrap(await api.POST('/api/v1/admin/leave-policies', { body })), onSuccess: invalidate })
}
export function useUpdatePolicy() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas['PolicyUpdate'] }) => unwrap(await api.PATCH('/api/v1/admin/leave-policies/{policy_id}', { params: { path: { policy_id: id } }, body })),
    onSuccess: invalidate,
  })
}
export function useDeletePolicy() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (id: string) => unwrap(await api.DELETE('/api/v1/admin/leave-policies/{policy_id}', { params: { path: { policy_id: id } } })), onSuccess: invalidate })
}

// --- holidays ---------------------------------------------------------------------------------------------
export function useHolidays(year?: number) {
  return useQuery({ queryKey: ['admin', 'holidays', year ?? 'all'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/holidays', { params: { query: year ? { year } : {} } })) })
}
export function useCreateHoliday() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (body: Schemas['HolidayCreate']) => unwrap(await api.POST('/api/v1/admin/holidays', { body })), onSuccess: invalidate })
}
export function useDeleteHoliday() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (id: string) => unwrap(await api.DELETE('/api/v1/admin/holidays/{holiday_id}', { params: { path: { holiday_id: id } } })), onSuccess: invalidate })
}
export function useImportHolidays() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (csv: string) => unwrap(await api.POST('/api/v1/admin/holidays/import', { body: { csv } })), onSuccess: invalidate })
}

// --- approval rules ---------------------------------------------------------------------------------------
export function useRules() {
  return useQuery({ queryKey: ['admin', 'rules'], queryFn: async () => unwrap(await api.GET('/api/v1/admin/approval-rules', { params: { query: { include_inactive: true } } })) })
}
export function useCreateRule() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (body: Schemas['RuleCreate']) => unwrap(await api.POST('/api/v1/admin/approval-rules', { body })), onSuccess: invalidate })
}
export function useUpdateRule() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({
    mutationFn: async ({ id, body }: { id: string; body: Schemas['RuleUpdate'] }) => unwrap(await api.PATCH('/api/v1/admin/approval-rules/{rule_id}', { params: { path: { rule_id: id } }, body })),
    onSuccess: invalidate,
  })
}
export function useDeleteRule() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (id: string) => unwrap(await api.DELETE('/api/v1/admin/approval-rules/{rule_id}', { params: { path: { rule_id: id } } })), onSuccess: invalidate })
}
export function useSimulate() {
  return useApiMutation({ mutationFn: async (body: Schemas['SimulateIn']) => unwrap(await api.POST('/api/v1/admin/approval-rules/simulate', { body })), silent: true })
}

// --- ledger and audit -------------------------------------------------------------------------------------
export function useTenantBalances(year: number) {
  return useQuery({ queryKey: ['admin', 'balances', year], queryFn: async () => unwrap(await api.GET('/api/v1/admin/balances', { params: { query: { year } } })) })
}
export function usePostAdjustment() {
  const invalidate = useInvalidate('admin')
  return useApiMutation({ mutationFn: async (body: Schemas['AdjustmentIn']) => unwrap(await api.POST('/api/v1/admin/ledger/adjustments', { body })), onSuccess: invalidate })
}
export function useAudit(params: { action?: string; from?: string; to?: string }) {
  return useQuery({
    queryKey: ['admin', 'audit', params],
    queryFn: async () => unwrap(await api.GET('/api/v1/admin/audit', { params: { query: { action: params.action || undefined, from: params.from || undefined, to: params.to || undefined, limit: 200 } } })),
  })
}
