import { Spin } from 'antd'
import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import { authEnabled } from '../auth/token'
import { usePrincipal } from '../auth/usePrincipal'
import { HealthPage } from '../features/system/HealthPage'
import { AppShell } from './AppShell'
import { RequireRole } from './RequireRole'

// Route-level code splitting: each workspace is a separate chunk (M0 follow-up).
const MyLeavePage = lazy(() => import('../features/leave/MyLeavePage'))
const ChatPage = lazy(() => import('../features/chat/ChatPage'))
const NotificationsPage = lazy(() => import('../features/notifications/NotificationsPage'))
const DocumentsPage = lazy(() => import('../features/documents/DocumentsPage'))
const ApprovalsPage = lazy(() => import('../features/approvals/ApprovalsPage'))
const TeamAvailabilityPage = lazy(() => import('../features/team/TeamAvailabilityPage'))
const TeamRequestsPage = lazy(() => import('../features/team/TeamRequestsPage'))
const EmployeesPage = lazy(() => import('../features/admin/EmployeesPage'))
const DepartmentsPage = lazy(() => import('../features/admin/DepartmentsPage'))
const LeaveCatalogPage = lazy(() => import('../features/admin/LeaveCatalogPage'))
const HolidaysPage = lazy(() => import('../features/admin/HolidaysPage'))
const ApprovalRulesPage = lazy(() => import('../features/admin/ApprovalRulesPage'))
const BalancesPage = lazy(() => import('../features/admin/BalancesPage'))
const AuditPage = lazy(() => import('../features/admin/AuditPage'))
const ReviewQueuePage = lazy(() => import('../features/review/ReviewQueuePage'))
const RetentionPage = lazy(() => import('../features/retention/RetentionPage'))
const TenantsPage = lazy(() => import('../features/platform/TenantsPage'))
const PlatformAuditPage = lazy(() => import('../features/platform/PlatformAuditPage'))

function Home() {
  const principal = usePrincipal()
  if (!authEnabled) return <Navigate to="/system" replace />
  if (!principal.me) return <RequireRole />
  if (principal.isTenantUser) return <Navigate to="/chat" replace />
  if (principal.isPlatformAdmin) return <Navigate to="/platform/tenants" replace />
  return <Navigate to="/system" replace />
}

const fallback = (
  <div style={{ padding: 48, textAlign: 'center' }}>
    <Spin size="large" />
  </div>
)

export function AppRouter() {
  return (
    <BrowserRouter>
      <Suspense fallback={fallback}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<Home />} />
            <Route path="/system" element={<HealthPage />} />
            <Route element={<RequireRole tenantOnly />}>
              <Route path="/chat" element={<ChatPage />} />
              <Route path="/leave" element={<MyLeavePage />} />
              <Route path="/notifications" element={<NotificationsPage />} />
              <Route path="/documents" element={<DocumentsPage />} />
            </Route>
            <Route element={<RequireRole anyOf={['manager', 'hr_admin']} tenantOnly />}>
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/team/availability" element={<TeamAvailabilityPage />} />
              <Route path="/team/requests" element={<TeamRequestsPage />} />
            </Route>
            <Route element={<RequireRole anyOf={['company_admin']} tenantOnly />}>
              <Route path="/admin/employees" element={<EmployeesPage />} />
              <Route path="/admin/departments" element={<DepartmentsPage />} />
              <Route path="/admin/leave-catalog" element={<LeaveCatalogPage />} />
              <Route path="/admin/holidays" element={<HolidaysPage />} />
              <Route path="/admin/approval-rules" element={<ApprovalRulesPage />} />
            </Route>
            <Route element={<RequireRole anyOf={['hr_admin', 'company_admin']} tenantOnly />}>
              <Route path="/admin/balances" element={<BalancesPage />} />
              <Route path="/admin/review-queue" element={<ReviewQueuePage />} />
              <Route path="/admin/retention" element={<RetentionPage />} />
              <Route path="/admin/audit" element={<AuditPage />} />
            </Route>
            <Route element={<RequireRole anyOf={['platform_admin']} />}>
              <Route path="/platform/tenants" element={<TenantsPage />} />
              <Route path="/platform/audit" element={<PlatformAuditPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}
