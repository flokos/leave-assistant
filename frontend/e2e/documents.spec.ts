import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import { findRow, signIn, signOut, USERS } from './helpers'

const TITLE = `E2E γονική άδεια ${Date.now()}`

test('HR uploads a document, it becomes searchable; employees never see HR-restricted documents', async ({ page }) => {
  test.setTimeout(240_000)
  // --- HR: upload a Markdown guide and wait for the pipeline -------------------------------------------
  await signIn(page, USERS.hr)
  await page.goto('/documents')
  await expect(page.getByTestId('documents-table')).toContainText('Κανονισμός Αδειών')
  await expect(page.getByTestId('documents-table')).toContainText('Οδηγός Εξαιρέσεων')
  await page.getByTestId('upload-document').click()
  await page.getByTestId('document-title').fill(TITLE)
  await page.getByTestId('document-file').locator('input[type="file"]').setInputFiles(fileURLToPath(new URL('./fixtures/faq-e2e.md', import.meta.url)))
  await page.getByRole('dialog').getByRole('button', { name: /upload document|ανέβασμα/i }).click()
  const row = await findRow(page, 'documents-table', TITLE)
  await expect(row).toBeVisible()
  await expect(row.getByTestId('status-ACTIVE')).toBeVisible({ timeout: 180_000 })
  await page.screenshot({ path: 'e2e/artifacts/documents-active.png', fullPage: true })

  // --- search (hybrid retrieval through the API) ---------------------------------------------------------
  await page.getByTestId('document-search').locator('input').fill('γονική άδεια')
  await page.keyboard.press('Enter')
  const hits = page.getByTestId('search-results')
  await expect(hits.getByTestId('search-hit').first()).toContainText(TITLE)
  await page.screenshot({ path: 'e2e/artifacts/documents-search.png', fullPage: true })

  // --- detail drawer: versions table and download button ---------------------------------------------------
  await hits.getByTestId('search-hit').first().click()
  const detail = page.getByTestId('document-detail')
  await expect(detail.getByTestId('versions-table')).toContainText('faq-e2e.md')
  await expect(detail.getByTestId('download-1')).toBeVisible()
  await page.keyboard.press('Escape')
  await signOut(page)

  // --- employee: sees the general document, not the HR-restricted guide -----------------------------------
  await signIn(page, USERS.employee)
  await page.goto('/documents')
  await expect(page.getByTestId('documents-table')).toContainText(TITLE)
  await expect(page.getByTestId('documents-table')).not.toContainText('Οδηγός Εξαιρέσεων')
  await expect(page.getByTestId('upload-document')).toHaveCount(0)
  await signOut(page)

  // --- HR: delete the E2E document so the seed stays reusable -------------------------------------------
  await signIn(page, USERS.hr)
  await page.goto('/documents')
  await (await findRow(page, 'documents-table', TITLE)).click()
  await page.getByTestId('delete-document').click()
  await page.getByRole('button', { name: /^(ok|yes|ναι)$/i }).click()
  await expect(page.getByTestId('documents-table')).not.toContainText(TITLE)
  await signOut(page)
})
