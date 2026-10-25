import { defineConfig, devices } from '@playwright/test'

/**
 * The scenario harness (E16 T5, docs/06-diseno-detallado.md:485, D-T16.4):
 * `web/e2e/scenarios/**` runs each wired scenario as the end-to-end test its
 * `expected` block promises, against a seminar stack that is already up (the
 * database, the api, the worker — `webServer` starts only the PWA itself).
 *
 * - One worker: scenarios share the one stack; a second browser mid-scenario
 *   would race the orgs and the outbox (D-T16.4).
 * - `reuseExistingServer` locally, so `just gate web/e2e/scenarios` uses the
 *   dev server a seminar already has open; CI always boots its own.
 * - A phone viewport: the PWA is the screen the farmer holds (docs/07).
 * - Retries cover the setup flake the blockers doc records (D1), and the
 *   findings they leave are read per run, never fixed here.
 */

const E2E_WEB_URL = process.env.E2E_WEB_URL ?? 'http://localhost:5173'
const E2E_API_ORIGIN = process.env.E2E_API_ORIGIN ?? 'http://localhost:8000'

export default defineConfig({
  testDir: './e2e/scenarios',
  // Playwright owns the specs; vitest owns the harness's own `*.test.ts`
  // units (its default include would otherwise collect them here, where the
  // vitest `describe` they import has no runner).
  testMatch: '**/*.spec.ts',
  timeout: 300_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: E2E_WEB_URL,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev',
    url: E2E_WEB_URL,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    env: { API_PROXY_TARGET: E2E_API_ORIGIN },
  },
  projects: [{ name: 'scenarios', use: { ...devices['Pixel 7'] } }],
})
