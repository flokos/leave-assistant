import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderOptions } from '@testing-library/react'
import { App as AntApp } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { AuthContext, type AuthContextProps } from 'react-oidc-context'
import { MemoryRouter } from 'react-router'
import './i18n'

interface Options extends Omit<RenderOptions, 'wrapper'> {
  auth?: Partial<AuthContextProps>
  route?: string
}

export function renderWithProviders(ui: ReactElement, { auth, route = '/', ...options }: Options = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const Wrapper = ({ children }: { children: ReactNode }) => {
    const tree = (
      <QueryClientProvider client={client}>
        <AntApp>
          <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
        </AntApp>
      </QueryClientProvider>
    )
    return auth ? <AuthContext.Provider value={auth as AuthContextProps}>{tree}</AuthContext.Provider> : tree
  }
  return { client, ...render(ui, { wrapper: Wrapper, ...options }) }
}

export const employeeMe = {
  user_id: 'sub-1',
  username: 'eleni.dimitriou@aegean-logistics.example',
  display_name: 'Ελένη Δημητρίου',
  roles: ['employee'],
  locale: 'el',
  is_platform_admin: false,
  is_automation: false,
  tenant: { id: 't', slug: 'aegean', name: 'Aegean Logistics S.A.', default_locale: 'el', timezone: 'Europe/Athens' },
  employee: {
    id: 'e',
    employee_number: 'AEG-004',
    first_name: 'Ελένη',
    last_name: 'Δημητρίου',
    email: 'eleni.dimitriou@aegean-logistics.example',
    hire_date: '2021-06-01',
    status: 'active',
    department: { id: 'd', code: 'OPS', name: 'Λειτουργίες' },
  },
  feature_flags: {},
  pending_interrupt_count: 0,
}

export const leaveTypes = [
  { id: 'lt-annual', code: 'annual', name_el: 'Κανονική άδεια', name_en: 'Annual leave', balance_bearing: true, allow_retroactive_days: 0, requires_document_above_days: null, active: true },
  { id: 'lt-sick', code: 'sick', name_el: 'Αναρρωτική άδεια', name_en: 'Sick leave', balance_bearing: true, allow_retroactive_days: 30, requires_document_above_days: 3, active: true },
]
