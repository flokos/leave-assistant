import { expect, test } from '@playwright/test'
import { signIn, signOut, USERS } from './helpers'

const cases: { name: keyof typeof USERS; expectMenu: string[]; forbid: string[] }[] = [
  { name: 'employee', expectMenu: ['My leave', 'Notifications'], forbid: ['Approvals', 'Employees', 'Companies'] },
  { name: 'manager', expectMenu: ['My leave', 'Approvals', 'Team availability'], forbid: ['Employees', 'Companies'] },
  { name: 'hr', expectMenu: ['Approvals', 'Team requests', 'Balances', 'Audit log'], forbid: ['Employees', 'Companies'] },
  { name: 'companyAdmin', expectMenu: ['Employees', 'Departments', 'Public holidays', 'Approval rules', 'Company settings', 'Security policy', 'Balances'], forbid: ['Approvals', 'Companies'] },
  { name: 'platform', expectMenu: ['Companies', 'Platform audit', 'Platform settings'], forbid: ['My leave', 'Approvals', 'Employees'] },
]

for (const c of cases) {
  test(`login as ${c.name} shows the right workspace`, async ({ page }) => {
    await signIn(page, USERS[c.name])
    const menu = page.getByTestId('workspace-menu')
    for (const label of c.expectMenu) await expect(menu.getByText(label, { exact: true })).toBeVisible()
    for (const label of c.forbid) await expect(menu.getByText(label, { exact: true })).toHaveCount(0)
    await page.screenshot({ path: `e2e/artifacts/${c.name}-workspace.png`, fullPage: true })
    await signOut(page)
  })
}

test('employee is told politely when opening an admin route', async ({ page }) => {
  await signIn(page, USERS.employee)
  await page.goto('/admin/employees')
  await expect(page.getByTestId('forbidden')).toBeVisible()
})
