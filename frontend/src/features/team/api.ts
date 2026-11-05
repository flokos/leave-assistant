import { useQuery } from '@tanstack/react-query'
import { api, type Schemas, unwrap } from '../../api/client'

export type AbsenceOut = Schemas['AbsenceOut']
export type TeamRequestOut = Schemas['TeamRequestOut']

export function useTeamEmployees() {
  return useQuery({ queryKey: ['team', 'employees'], queryFn: async () => unwrap(await api.GET('/api/v1/team/employees')) })
}

export function useAvailability(from: string, to: string) {
  return useQuery({
    queryKey: ['team', 'availability', from, to],
    queryFn: async () => unwrap(await api.GET('/api/v1/team/availability', { params: { query: { from, to } } })),
  })
}

export function useTeamRequests(statuses: string[] | undefined) {
  return useQuery({
    queryKey: ['team', 'requests', statuses ?? []],
    queryFn: async () => unwrap(await api.GET('/api/v1/team/requests', { params: { query: { status: statuses as never, limit: 200 } } })),
  })
}
