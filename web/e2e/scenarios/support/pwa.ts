/**
 * The PWA side the scenario specs share (docs/07's screens, E16 T6): the
 * sign-in the PWA owns and the remembered active plot. Kept apart from
 * harness.ts, which stays pure of Playwright on purpose (D-T16.9).
 */

import { expect, type Page } from '@playwright/test'

import { apiLogOffset, readOtpCodeAfter } from './harness'

/** The PWA's sign-in: phone → code (the api log is where the code is) → the
 * single-org member lands on Inicio. Resolves once the home's plot identity
 * is visible. */
export async function signIn(page: Page, phone: string, plotNamePrefix: string): Promise<void> {
  await page.goto('/')
  await page.getByLabel('Teléfono').fill(phone)
  const otpBefore = await apiLogOffset()
  await page.getByRole('button', { name: 'Enviar código' }).click()
  const code = await readOtpCodeAfter(phone, otpBefore)
  await page.getByLabel('Código').fill(code)
  await page.getByRole('button', { name: 'Verificar' }).click()
  // The scenario plot's own name is the screen's identity (the loader named
  // the plot "Escenario <scenario> <ancla>", docs/04:263).
  await expect(page.getByText(plotNamePrefix).first()).toBeVisible()
}

/** The remembered active plot points the home screen at the scenario's own
 * parcel — the way the plot listing would after the farmer taps it (the org
 * holds sim run's "Lote 1" beside the loader's scenario plot). Call before
 * `signIn`. */
export function rememberActivePlot(page: Page, orgId: string, plotId: string): void {
  void page.addInitScript(
    ({ org, plot }) => {
      localStorage.setItem('techcamp.active_plot', JSON.stringify({ [org]: plot }))
    },
    { org: orgId, plot: plotId },
  )
}
