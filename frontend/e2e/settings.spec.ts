import { expect, test } from '@playwright/test'
import { signIn, signOut, USERS } from './helpers'

// M10: company settings, security policy, platform settings and the audit CSV export, against the real stack.
// Every value changed here is put back, so the seed stays as it was.

test('company administrator changes the card lifetime and the security policy, then exports the audit log', async ({ page }) => {
  await signIn(page, USERS.companyAdmin)
  await page.goto('/admin/settings')
  const ttl = page.getByTestId('settings-proposal-ttl')
  await expect(ttl).toHaveValue('15')
  await ttl.fill('20')
  await page.getByTestId('settings-save').click()
  await expect(page.getByText('Saved')).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('settings-proposal-ttl')).toHaveValue('20')
  await page.getByTestId('settings-proposal-ttl').fill('15')
  await page.getByTestId('settings-save').click()
  await expect(page.getByText('Saved').first()).toBeVisible()

  await page.goto('/admin/security-policy')
  const low = page.getByTestId('security-risk-low')
  await expect(low).toHaveValue('0.30')
  await low.fill('0.8') // above risk_medium_max: the backend refuses it
  await page.getByTestId('security-save').click()
  await expect(page.getByTestId('problem-alert')).toContainText('risk_medium_max')
  await low.fill('0.3')
  await page.getByTestId('security-save').click()
  await expect(page.getByText('Saved')).toBeVisible()

  await page.goto('/admin/audit')
  await page.getByPlaceholder('Action').fill('admin.tenant.settings_changed')
  const download = page.waitForEvent('download')
  await page.getByTestId('audit-export').click()
  const file = await download
  expect(file.suggestedFilename()).toMatch(/^audit-tenant-\d{4}-\d{2}-\d{2}\.csv$/)
  const text = (await (await file.createReadStream()).toArray()).map((b) => b.toString()).join('')
  expect(text).toContain('admin.tenant.settings_changed')
  await signOut(page)
})

test('platform administrator sees and saves the platform settings', async ({ page }) => {
  await signIn(page, USERS.platform)
  await page.goto('/platform/settings')
  await expect(page.getByTestId('security-threshold-max')).toHaveValue('0.95')
  const retention = page.getByTestId('settings-retention')
  await expect(retention).not.toHaveValue('')
  const before = await retention.inputValue()
  await retention.fill('120')
  await page.getByTestId('platform-settings-save').click()
  await expect(page.getByText('Saved')).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('settings-retention')).toHaveValue('120')
  await page.getByTestId('settings-retention').fill(before)
  await page.getByTestId('platform-settings-save').click()
  await expect(page.getByText('Saved').first()).toBeVisible()
  await signOut(page)
})
