import { expect, type Page } from '@playwright/test'

export const PASSWORD = process.env.SEED_USER_PASSWORD ?? 'leave-dev'
export const USERS = {
  platform: 'platform.admin',
  companyAdmin: 'giorgos.alexiou@aegean-logistics.example',
  hr: 'maria.oikonomou@aegean-logistics.example',
  manager: 'nikos.papadakis@aegean-logistics.example',
  employee: 'eleni.dimitriou@aegean-logistics.example',
} as const

/** PKCE login through the real Keycloak page; lands back on the app with the workspace menu rendered. */
export async function signIn(page: Page, username: string): Promise<void> {
  // a sign-out's redirect chain may still be in flight (ERR_ABORTED): settle, then navigate again
  await page.goto('/').catch(async () => {
    await page.waitForLoadState('load').catch(() => undefined)
    await page.goto('/')
  })
  await page.getByTestId('sign-in').first().click()
  await page.locator('#username').fill(username)
  await page.locator('#password').fill(PASSWORD)
  await page.locator('#kc-login').click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

/** Signs out through Keycloak's end-session redirect and drops the SSO cookies, so the next sign-in shows the form. */
export async function signOut(page: Page): Promise<void> {
  await page.keyboard.press('Escape') // close any drawer/modal left open by the scenario
  await page.mouse.move(0, 0) // a fresh pointer move is what opens the hover dropdown
  const menu = page.getByTestId('user-menu')
  const item = page.getByRole('menuitem', { name: /sign out|αποσύνδεση/i })
  await menu.click() // the user menu opens on click (and on hover)
  await item.click()
  await expect(page.getByTestId('sign-in').first()).toBeVisible()
  await page.waitForLoadState('networkidle')
  await page.context().clearCookies()
}
