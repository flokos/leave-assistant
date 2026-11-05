import { expect, test } from '@playwright/test'
import { signIn, signOut, USERS } from './helpers'

test('company administrator adds and deletes a holiday; switches the UI to Greek', async ({ page }) => {
  await signIn(page, USERS.companyAdmin)
  await page.goto('/admin/holidays')
  await page.getByTestId('new-holiday').click()
  await page.getByTestId('holiday-date').locator('input').fill('2026-12-31')
  await page.keyboard.press('Enter')
  await page.getByTestId('holiday-name-el').fill('Παραμονή Πρωτοχρονιάς (E2E)')
  await page.getByTestId('holiday-name-en').fill("New Year's Eve (E2E)")
  await page.getByRole('button', { name: /^ok$/i }).click()
  await expect(page.getByTestId('holidays-table')).toContainText("New Year's Eve (E2E)")
  await page.getByTestId('delete-holiday-2026-12-31').click()
  await page.getByRole('button', { name: /^(ok|yes)$/i }).click()
  await expect(page.getByTestId('holidays-table')).not.toContainText("New Year's Eve (E2E)")

  await page.getByTestId('language-toggle').getByText('EL').click()
  await expect(page.getByTestId('workspace-menu')).toContainText('Εργαζόμενοι')
  await page.screenshot({ path: 'e2e/artifacts/admin-greek.png', fullPage: true })
  await page.getByTestId('language-toggle').getByText('EN').click()
  await signOut(page)
})
