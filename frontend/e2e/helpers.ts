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
  await page.goto('/')
  await page.getByTestId('sign-in').first().click()
  await page.locator('#username').fill(username)
  await page.locator('#password').fill(PASSWORD)
  await page.locator('#kc-login').click()
  await expect(page.getByTestId('user-menu')).toBeVisible()
}

export async function signOut(page: Page): Promise<void> {
  await page.getByTestId('user-menu').hover()
  await page.getByRole('menuitem', { name: /sign out|αποσύνδεση/i }).click()
  await expect(page.getByTestId('sign-in').first()).toBeVisible()
}
