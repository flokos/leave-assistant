import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import { findRow, signIn, signOut, USERS } from './helpers'

const START = '2026-12-07'
const END = '2026-12-08'

test('a footnote addressed to the assistant waits for review; HR inspects and releases it', async ({ page }) => {
  test.setTimeout(420_000)
  const title = `E2E υποσημείωση ${Date.now()}`
  await signIn(page, USERS.hr)
  await page.goto('/admin/review-queue')
  // the seeded quarantined English document stays held back
  await expect(page.getByTestId('review-table').locator('tr', { hasText: 'Leave approvals appendix (untrusted upload)' }).getByTestId('tier-high')).toBeVisible()
  await page.screenshot({ path: 'e2e/artifacts/review-queue.png', fullPage: true })
  await page.goto('/documents')
  await page.getByTestId('upload-document').click()
  await page.getByTestId('document-title').fill(title)
  await page.getByTestId('document-file').locator('input[type="file"]').setInputFiles(fileURLToPath(new URL('./fixtures/footnote-e2e.md', import.meta.url)))
  await page.getByRole('dialog').getByRole('button', { name: /upload document|ανέβασμα/i }).click()
  const library = await findRow(page, 'documents-table', title)
  await expect(library.getByTestId('status-PENDING_REVIEW')).toBeVisible({ timeout: 360_000 })
  await page.goto('/admin/review-queue')
  const row = page.getByTestId('review-table').locator('tr', { hasText: title })
  await expect(row.getByTestId('tier-medium')).toBeVisible()
  await row.getByRole('button', { name: /expand row/i }).click()
  await expect(page.getByTestId(/findings-/)).toContainText(/Hidden instruction|Κρυφή οδηγία/)
  await row.getByTestId('inspect-1').click()
  await page.getByRole('dialog').getByTestId('review-comment').fill('inspected (E2E)')
  await page.getByRole('dialog').getByRole('button', { name: /inspect|επιθεώρηση/i }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await row.getByTestId('release-1').click()
  await page.getByRole('dialog').getByTestId('review-comment').fill('benign footnote, released (E2E)')
  await page.getByRole('dialog').getByRole('button', { name: /release|απελευθέρωση/i }).click()
  await expect(page.getByTestId('review-table').locator('tr', { hasText: title })).toHaveCount(0)
  await page.goto('/documents')
  await (await findRow(page, 'documents-table', title)).getByTestId('status-ACTIVE').click()
  await page.getByTestId('delete-document').click()
  await page.getByRole('button', { name: /^(ok|yes|ναι)$/i }).click()
  await expect(page.getByTestId('documents-table')).not.toContainText(title)
  await signOut(page)
})

test('employee attaches a supporting document; HR downloads it with a reason; the manager never sees it', async ({ page }) => {
  test.setTimeout(180_000)
  // --- employee: new sick request + attachment ------------------------------------------------------------
  await signIn(page, USERS.employee)
  await page.goto('/leave') // the tenant home is the assistant (M6)
  await page.getByTestId('new-request').click()
  const wizard = page.getByTestId('request-wizard')
  await wizard.getByTestId('leave-type').click()
  await page.locator('.ant-select-item-option', { hasText: /sick|αναρρωτική/i }).click()
  await wizard.getByTestId('dates').locator('input').first().fill(START)
  await page.keyboard.press('Enter')
  await wizard.getByTestId('dates').locator('input').nth(1).fill(END)
  await page.keyboard.press('Enter')
  await wizard.getByTestId('reason').fill('E2E supporting document')
  await wizard.getByTestId('validate').click()
  await wizard.getByTestId('submit').click()
  const requestNumber = (await wizard.getByTestId('submitted').innerText()).match(/LR-\d{4}-\d{6}/)?.[0]
  expect(requestNumber).toBeTruthy()
  await page.keyboard.press('Escape')
  const myRow = await findRow(page, 'requests-table', requestNumber as string)
  await myRow.click()
  const detail = page.getByTestId('request-detail')
  const fileInput = detail.getByTestId('supporting-upload').locator('input[type="file"]')
  await fileInput.setInputFiles(fileURLToPath(new URL('../../backend/src/leave_assistant/seed/corpus/supporting/medical-note.png', import.meta.url)))
  await expect(detail.getByTestId('supporting-table')).toContainText('medical-note.png')
  await page.screenshot({ path: 'e2e/artifacts/supporting-attached.png', fullPage: true })
  await page.keyboard.press('Escape')
  await signOut(page)

  // --- HR: sees the attachment in the detail, downloads with a reason ---------------------------------------
  await signIn(page, USERS.hr)
  await page.goto('/team/requests')
  const hrRow = await findRow(page, 'team-requests-table', requestNumber as string)
  await hrRow.click()
  const hrDetail = page.getByTestId('request-detail')
  await expect(hrDetail.getByTestId('supporting-table')).toContainText('medical-note.png')
  const downloadPromise = page.waitForEvent('download')
  await hrDetail.getByTestId(/supporting-download-/).click()
  await page.getByRole('dialog').getByTestId('supporting-reason').fill('verifying the certificate (E2E)')
  await page.getByRole('dialog').getByRole('button', { name: 'OK' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toContain('medical-note')
  await page.screenshot({ path: 'e2e/artifacts/supporting-hr.png', fullPage: true })
  await page.keyboard.press('Escape')
  await signOut(page)

  // --- manager: the request detail has no supporting-documents panel ----------------------------------------
  await signIn(page, USERS.manager)
  await page.goto('/team/requests')
  const managerRow = await findRow(page, 'team-requests-table', requestNumber as string)
  await managerRow.click()
  await expect(page.getByTestId('request-detail')).toBeVisible()
  await expect(page.getByTestId('supporting-documents')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await signOut(page)

  // --- employee: cancel the request so the seed stays reusable ---------------------------------------------
  await signIn(page, USERS.employee)
  await page.goto('/leave')
  await (await findRow(page, 'requests-table', requestNumber as string)).click()
  await page.getByTestId('cancel-request').click()
  await page.getByRole('button', { name: /^(ok|yes|ναι)$/i }).click()
  await expect(page.getByTestId('request-detail').getByTestId('status-CANCELLED')).toBeVisible()
  await page.keyboard.press('Escape')
  await signOut(page)
})
