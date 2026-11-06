import { expect, test } from '@playwright/test'
import { signIn, signOut, USERS } from './helpers'

// Runs against the live stack with LLM_PROVIDER_ORDER=fake (no API keys needed): retrieval, reranking, Presidio,
// the graph, SSE and re-identification are real; only the language model is the deterministic fake.
test('employee asks a policy question in Greek and opens the cited source', async ({ page }) => {
  test.setTimeout(120_000)
  await signIn(page, USERS.employee)
  await expect(page).toHaveURL(/\/chat$/) // the assistant is the tenant home
  await page.getByTestId('new-conversation').click()
  await page.getByTestId('chat-input').fill('Πόσες μέρες κανονικής άδειας δικαιούμαι τον χρόνο;')
  await page.getByTestId('chat-send').click()
  const pane = page.getByTestId('chat-pane')
  const answer = pane.getByTestId('message-assistant').last()
  await expect(answer.getByTestId('sources')).toBeVisible({ timeout: 90_000 })
  await expect(answer.getByTestId('sources')).toContainText(/Κανονισμός Αδειών|Συχνές ερωτήσεις|Leave Policy/)
  await page.screenshot({ path: 'e2e/artifacts/chat-answer.png', fullPage: true })
  await answer.getByTestId('cite-1').first().click() // a model may cite the same source twice
  await expect(page.getByTestId('citation-drawer')).toBeVisible()
  await page.keyboard.press('Escape')
  // an off-corpus question gets an honest template, not an invented answer: the live classifier may route it to the
  // knowledge path (insufficient evidence) or call it out of scope — both are correct refusals
  await page.getByTestId('chat-input').fill('Ποια είναι η πολιτική για κατοικίδια στο γραφείο;')
  await page.getByTestId('chat-send').click()
  await expect(pane.getByTestId('outcome-insufficient_evidence').or(pane.getByTestId('outcome-scope'))).toBeVisible({ timeout: 90_000 })
  // the history survives a reload
  await page.reload()
  await expect(page.getByTestId('chat-pane').getByTestId('message-assistant')).toHaveCount(2)
  await page.getByTestId('conversation-item').first().getByRole('img', { name: 'delete' }).click()
  await page.getByRole('button', { name: /^(ok|yes|ναι)$/i }).click()
  await signOut(page)
})
