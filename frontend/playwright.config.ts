import { defineConfig, devices } from '@playwright/test'

// E2E smoke against the running Docker stack (make up-staged && make bootstrap). Not part of CI (Q-13).
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'e2e/artifacts/report' }]],
  outputDir: 'e2e/artifacts/results',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8080',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    locale: 'en-GB',
    ...devices['Desktop Chrome'],
  },
})
