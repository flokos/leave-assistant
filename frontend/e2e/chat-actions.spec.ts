import { expect, test } from '@playwright/test'
import { findRow, signIn, signOut } from './helpers'

// Brightwater users: the other chat spec signs in as Eleni in parallel
const EMPLOYEE = 'sean.obrien@brightwater.example'
const MANAGER = 'liam.murphy@brightwater.example'

// M7 on the live stack with the fake model: the employee books leave through the assistant and confirms the card,
// the manager approves it in their own conversation, the employee cancels it in the UI (keeps the seed repeatable).
test('employee books through the chat, the manager approves in chat, the employee cancels', async ({ page }) => {
  test.setTimeout(240_000)
  await signIn(page, EMPLOYEE)
  await page.getByTestId('new-conversation').click()
  await page.getByTestId('chat-input').fill('I want 2 days off starting December 9')
  await page.getByTestId('chat-send').click()
  const pane = page.getByTestId('chat-pane')
  const card = pane.getByTestId('proposal-card').last()
  await expect(card).toBeVisible({ timeout: 120_000 })
  await expect(card).toContainText('2026-12-09 – 2026-12-10')
  await expect(card.getByTestId('card-status')).toContainText(/Waiting for you|Περιμένει εσάς/)
  await page.screenshot({ path: 'e2e/artifacts/chat-card.png', fullPage: true })
  await card.getByTestId('card-confirm').click()
  await expect(pane.getByTestId('outcome-executed')).toBeVisible({ timeout: 60_000 })
  const outcome = await pane.getByTestId('message-assistant').last().innerText()
  const requestNumber = outcome.match(/LR-\d{4}-\d{6}/)?.[0]
  expect(requestNumber).toBeTruthy()
  await expect(pane.getByTestId('proposal-card').last().getByTestId('card-status')).toContainText(/Done|Ολοκληρώθηκε/)
  await page.screenshot({ path: 'e2e/artifacts/chat-executed.png', fullPage: true })
  await signOut(page)

  await signIn(page, MANAGER)
  await page.getByTestId('new-conversation').click()
  await page.getByTestId('chat-input').fill(`Approve request ${requestNumber}`)
  await page.getByTestId('chat-send').click()
  const approval = page.getByTestId('chat-pane').getByTestId('proposal-card').last()
  await expect(approval).toBeVisible({ timeout: 120_000 })
  await expect(approval).toContainText("Sean O'Brien") // the approver's card names the requester
  await approval.getByTestId('card-confirm').click()
  await expect(page.getByTestId('chat-pane').getByTestId('outcome-executed')).toBeVisible({ timeout: 60_000 })
  await page.screenshot({ path: 'e2e/artifacts/chat-approved.png', fullPage: true })
  await signOut(page)

  await signIn(page, EMPLOYEE)
  await page.goto('/leave')
  const row = await findRow(page, 'requests-table', requestNumber as string)
  await expect(row.getByTestId('status-APPROVED')).toBeVisible()
  await row.click()
  await page.getByTestId('cancel-request').click()
  await page.getByRole('button', { name: /^(ok|yes|ναι)$/i }).click()
  await expect(page.getByTestId('request-detail').getByTestId('status-CANCELLED')).toBeVisible()
  await signOut(page)
})
