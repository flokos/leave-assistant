import { expect, test } from '@playwright/test'
import { signIn, signOut, USERS } from './helpers'

const START = '2026-11-30'
const END = '2026-12-01'

test('employee submits, manager approves, employee sees the approval and cancels', async ({ page }) => {
  // --- employee: wizard validate → submit --------------------------------------------------------------
  await signIn(page, USERS.employee)
  await page.getByTestId('new-request').click()
  const wizard = page.getByTestId('request-wizard')
  await wizard.getByTestId('dates').locator('input').first().fill(START)
  await page.keyboard.press('Enter')
  await wizard.getByTestId('dates').locator('input').nth(1).fill(END)
  await page.keyboard.press('Enter')
  await wizard.getByTestId('reason').fill('E2E smoke')
  await wizard.getByTestId('validate').click()
  const report = wizard.getByTestId('validation-report')
  await expect(report).toContainText('2') // working days
  await expect(report).toContainText('Manager')
  await wizard.getByTestId('submit').click()
  await expect(wizard.getByTestId('submitted')).toContainText('Pending manager')
  const requestNumber = (await wizard.getByTestId('submitted').innerText()).match(/LR-\d{4}-\d{6}/)?.[0]
  expect(requestNumber).toBeTruthy()
  await page.screenshot({ path: 'e2e/artifacts/flow-submitted.png' })
  await page.keyboard.press('Escape')
  await signOut(page)

  // --- manager: approve from the inbox ------------------------------------------------------------------
  await signIn(page, USERS.manager)
  await page.goto('/approvals')
  const row = page.getByTestId('pending-table').locator('tr', { hasText: requestNumber as string })
  await expect(row).toBeVisible()
  await row.getByTestId('approve').click()
  const dialog = page.getByRole('dialog')
  await dialog.getByTestId('decision-comment').fill('ok (E2E)')
  await dialog.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByTestId('pending-table').locator('tr', { hasText: requestNumber as string })).toHaveCount(0)
  await page.screenshot({ path: 'e2e/artifacts/flow-approved.png' })
  await signOut(page)

  // --- employee: sees APPROVED, cancels (keeps the seed data repeatable) -------------------------------
  await signIn(page, USERS.employee)
  const myRow = page.getByTestId('requests-table').locator('tr', { hasText: requestNumber as string })
  await expect(myRow.getByTestId('status-APPROVED')).toBeVisible()
  await myRow.click()
  await page.getByTestId('cancel-request').click()
  await page.getByRole('button', { name: /^(ok|yes|ναι)$/i }).click()
  await expect(page.getByTestId('request-detail').getByTestId('status-CANCELLED')).toBeVisible()
  await page.screenshot({ path: 'e2e/artifacts/flow-cancelled.png' })
  await page.keyboard.press('Escape')
  await signOut(page)
})
