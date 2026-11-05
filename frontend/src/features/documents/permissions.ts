import type { DocumentCategory } from './api'

/** Categories a role may upload to (ADR-0027) — a UI convenience; the API re-checks. */
export function uploadCategoriesFor(roles: string[]): DocumentCategory[] {
  const allowed = new Set<DocumentCategory>()
  if (roles.includes('hr_admin')) ['policy_general', 'policy_hr_restricted'].forEach((c) => allowed.add(c as DocumentCategory))
  if (roles.includes('company_admin')) ['policy_general', 'admin_internal'].forEach((c) => allowed.add(c as DocumentCategory))
  if (roles.includes('manager')) allowed.add('department_notice')
  return [...allowed]
}
