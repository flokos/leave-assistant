import { expect, type Page, test } from '@playwright/test'
import { findRow, signIn, signOut } from './helpers'

// Brightwater users (the Aegean specs run in parallel): the company administrator connects the mock company
// calendar, an approval is synchronised by the worker, a cancellation removes the event, and the calendar is
// disconnected again so the run is repeatable (M8, doc 10).
const ADMIN = 'aoife.byrne@brightwater.example'
const EMPLOYEE = 'niamh.kelly@brightwater.example'
const MANAGER = 'liam.murphy@brightwater.example'
const START = '2026-12-21'
const END = '2026-12-22'

/** Opens the calendar page once the connections are loaded (the card reads "Not connected" while loading). */
async function openCalendar(page: Page): Promise<void> {
  const loaded = page.waitForResponse((r) => r.url().endsWith('/api/v1/calendar/connections') && r.ok())
  await page.goto('/calendar')
  await loaded
}

async function disconnectIfConnected(page: Page): Promise<void> {
  const card = page.getByTestId('connection-company_shared')
  await expect(card).toBeVisible()
  const disconnect = card.getByTestId('disconnect-company_shared')
  if (await disconnect.isVisible()) {
    await disconnect.click()
    await page.getByRole('button', { name: /^(ok|yes|ναι)$/i }).click()
    await expect(card.getByTestId('connect-company_shared')).toBeVisible()
  }
}

async function jobStatus(page: Page, requestNumber: string, operation: 'create' | 'delete', status: RegExp): Promise<void> {
  await expect(async () => {
    await page.reload()
    const row = await findRow(page, 'sync-jobs', requestNumber)
    await expect(row.getByTestId(`job-status-${requestNumber}-${operation}`)).toHaveText(status, { timeout: 2_000 })
  }).toPass({ timeout: 90_000, intervals: [2_000, 3_000, 5_000] })
}

test('company calendar: connect (mock consent), sync an approval, remove it on cancellation, disconnect', async ({ page }) => {
  test.setTimeout(300_000)
  // --- company admin connects the company calendar through the (simulated) consent --------------------------
  await signIn(page, ADMIN)
  await openCalendar(page)
  await disconnectIfConnected(page) // a previous failed run may have left it connected
  await page.getByTestId('connect-company_shared').click()
  await page.waitForURL(/\/calendar/)
  const card = page.getByTestId('connection-company_shared')
  await expect(card.getByTestId('connection-status-company_shared')).toHaveText(/Active|Ενεργό/, { timeout: 30_000 })
  await page.screenshot({ path: 'e2e/artifacts/calendar-connected.png', fullPage: true })
  await signOut(page)

  // --- employee submits, manager approves ------------------------------------------------------------------
  await signIn(page, EMPLOYEE)
  await page.goto('/leave')
  await page.getByTestId('new-request').click()
  const wizard = page.getByTestId('request-wizard')
  await wizard.getByTestId('dates').locator('input').first().fill(START)
  await page.keyboard.press('Enter')
  await wizard.getByTestId('dates').locator('input').nth(1).fill(END)
  await page.keyboard.press('Enter')
  await wizard.getByTestId('validate').click()
  await expect(wizard.getByTestId('validation-report')).toBeVisible()
  await wizard.getByTestId('submit').click()
  await expect(wizard.getByTestId('submitted')).toBeVisible()
  const requestNumber = (await wizard.getByTestId('submitted').innerText()).match(/LR-\d{4}-\d{6}/)?.[0] as string
  expect(requestNumber).toBeTruthy()
  await page.keyboard.press('Escape')
  await signOut(page)

  await signIn(page, MANAGER)
  await page.goto('/approvals')
  const pending = page.getByTestId('pending-table').locator('tr', { hasText: requestNumber })
  await pending.getByTestId('approve').click()
  await page.getByRole('dialog').getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByTestId('pending-table').locator('tr', { hasText: requestNumber })).toHaveCount(0)
  await signOut(page)

  // --- the worker synchronised it: the admin sees the job; the employee sees their own -----------------------
  await signIn(page, ADMIN)
  await page.goto('/calendar')
  await jobStatus(page, requestNumber, 'create', /Synchronised|Συγχρονίστηκε/)
  await page.screenshot({ path: 'e2e/artifacts/calendar-synced.png', fullPage: true })
  // a new company calendar lacks the absences approved before it was connected: reconciliation re-creates them
  // automatically (resolved items) and leaves nothing open
  await page.getByTestId('reconcile').click()
  const reconciliation = page.getByTestId('reconciliation')
  await expect(reconciliation.getByText(/Last run|Τελευταία εκτέλεση/)).toBeVisible({ timeout: 30_000 })
  await expect(reconciliation.getByText(/^(Open|Ανοιχτό)$/)).toHaveCount(0)
  await signOut(page)

  // --- the employee cancels: the event is removed ----------------------------------------------------------
  await signIn(page, EMPLOYEE)
  await page.goto('/leave')
  const mine = await findRow(page, 'requests-table', requestNumber)
  await mine.click()
  await page.getByTestId('cancel-request').click()
  await page.getByRole('button', { name: /^(ok|yes|ναι)$/i }).click()
  await expect(page.getByTestId('request-detail').getByTestId('status-CANCELLED')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.goto('/calendar')
  await jobStatus(page, requestNumber, 'delete', /Synchronised|Συγχρονίστηκε/)
  await expect(page.getByTestId('reconciliation')).toHaveCount(0) // employees do not reconcile
  await signOut(page)

  // --- clean up: disconnect ----------------------------------------------------------------------------------
  await signIn(page, ADMIN)
  await openCalendar(page)
  await disconnectIfConnected(page)
  await signOut(page)
})
