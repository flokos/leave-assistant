import {
  ApartmentOutlined,
  AuditOutlined,
  BankOutlined,
  BellOutlined,
  CalendarOutlined,
  CheckSquareOutlined,
  ClusterOutlined,
  DashboardOutlined,
  DeleteOutlined,
  FileDoneOutlined,
  FileTextOutlined,
  MessageOutlined,
  SafetyCertificateOutlined,
  ScheduleOutlined,
  SettingOutlined,
  TeamOutlined,
  UserOutlined,
  ControlOutlined,
  SafetyOutlined,
} from '@ant-design/icons'
import type { MenuProps } from 'antd'
import { Link } from 'react-router'
import type { Principal } from '../auth/usePrincipal'

type MenuItem = NonNullable<MenuProps['items']>[number]

/** The workspace menu of brief §11, filtered by the roles the backend reported. */
export function menuItemsFor(principal: Principal, t: (key: string) => string): MenuItem[] {
  const groups: MenuItem[] = []
  const group = (key: string, label: string, children: MenuItem[]) =>
    children.length ? groups.push({ key, type: 'group', label, children }) : undefined
  if (principal.isTenantUser) {
    group('employee', t('nav.employee'), [
      { key: '/chat', icon: <MessageOutlined />, label: <Link to="/chat">{t('nav.assistant')}</Link> },
      { key: '/leave', icon: <ScheduleOutlined />, label: <Link to="/leave">{t('nav.myLeave')}</Link> },
      { key: '/notifications', icon: <BellOutlined />, label: <Link to="/notifications">{t('nav.notifications')}</Link> },
      { key: '/documents', icon: <FileTextOutlined />, label: <Link to="/documents">{t('nav.documents')}</Link> },
      { key: '/calendar', icon: <CalendarOutlined />, label: <Link to="/calendar">{t('nav.calendar')}</Link> },
    ])
  }
  if (principal.hasRole('manager', 'hr_admin')) {
    group('manager', principal.hasRole('hr_admin') ? t('nav.hr') : t('nav.manager'), [
      { key: '/approvals', icon: <CheckSquareOutlined />, label: <Link to="/approvals">{t('nav.approvals')}</Link> },
      { key: '/team/availability', icon: <CalendarOutlined />, label: <Link to="/team/availability">{t('nav.teamAvailability')}</Link> },
      { key: '/team/requests', icon: <TeamOutlined />, label: <Link to="/team/requests">{t('nav.teamRequests')}</Link> },
    ])
  }
  const admin: MenuItem[] = []
  if (principal.hasRole('company_admin')) {
    admin.push(
      { key: '/admin/employees', icon: <UserOutlined />, label: <Link to="/admin/employees">{t('nav.employees')}</Link> },
      { key: '/admin/departments', icon: <ApartmentOutlined />, label: <Link to="/admin/departments">{t('nav.departments')}</Link> },
      { key: '/admin/leave-catalog', icon: <SettingOutlined />, label: <Link to="/admin/leave-catalog">{t('nav.leaveCatalog')}</Link> },
      { key: '/admin/holidays', icon: <CalendarOutlined />, label: <Link to="/admin/holidays">{t('nav.holidays')}</Link> },
      { key: '/admin/approval-rules', icon: <FileDoneOutlined />, label: <Link to="/admin/approval-rules">{t('nav.approvalRules')}</Link> },
      { key: '/admin/settings', icon: <ControlOutlined />, label: <Link to="/admin/settings">{t('nav.companySettings')}</Link> },
      { key: '/admin/security-policy', icon: <SafetyOutlined />, label: <Link to="/admin/security-policy">{t('nav.securityPolicy')}</Link> },
    )
  }
  if (principal.hasRole('hr_admin', 'company_admin')) {
    admin.push(
      { key: '/admin/balances', icon: <DashboardOutlined />, label: <Link to="/admin/balances">{t('nav.balances')}</Link> },
      { key: '/admin/review-queue', icon: <SafetyCertificateOutlined />, label: <Link to="/admin/review-queue">{t('nav.reviewQueue')}</Link> },
      { key: '/admin/retention', icon: <DeleteOutlined />, label: <Link to="/admin/retention">{t('nav.retention')}</Link> },
      { key: '/admin/audit', icon: <AuditOutlined />, label: <Link to="/admin/audit">{t('nav.audit')}</Link> },
    )
  }
  group('admin', t('nav.companyAdmin'), admin)
  if (principal.isPlatformAdmin) {
    group('platform', t('nav.platform'), [
      { key: '/platform/tenants', icon: <BankOutlined />, label: <Link to="/platform/tenants">{t('nav.tenants')}</Link> },
      { key: '/platform/audit', icon: <AuditOutlined />, label: <Link to="/platform/audit">{t('nav.platformAudit')}</Link> },
      { key: '/platform/settings', icon: <ControlOutlined />, label: <Link to="/platform/settings">{t('nav.platformSettings')}</Link> },
    ])
  }
  group('system', t('nav.system'), [
    { key: '/system', icon: <ClusterOutlined />, label: <Link to="/system">{t('nav.status')}</Link> },
  ])
  return groups
}
