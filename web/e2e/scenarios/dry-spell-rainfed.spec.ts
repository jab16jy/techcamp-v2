/**
 * Scenario E (`dry-spell-rainfed`, docs/06:459-471) end to end. E is the one
 * scenario the merged stack runs without a broker: no nodes, so the loader
 * (T3) prepares base data + fixture, the irrigation jobs replay the
 * scenario's 21 days (what the 04:30 cron does across the scenario's real
 * days), and the balance sweep runs through the real worker. The T4 checker
 * judges the `expected` block and its verdict is asserted green: the owner's
 * sowing-age decision (#340, 2026-10-25 — E sows 55 days before its anchor,
 * inside the catalog's mid stage) restored the docs' arithmetic, so the
 * balance passes RAW on day 16 and the oracle holds.
 *
 * Every wait polls `expected` state with the named intervals of D-T16.4.
 * `@pr`: this scenario runs per PR (D-T16.4's split), nightly too.
 */

import { expect, devices, test } from '@playwright/test'

import {
  E2E_TIMING,
  checkExpected,
  deferBalanceSweep,
  firstOrgId,
  loadScenario,
  login,
  plotStatus,
  replayIrrigationDays,
  runSimScenario,
  waitForApi,
} from './support/harness'
import { rememberActivePlot, signIn } from './support/pwa'

test.use({ ...devices['Pixel 7'] })

const SCENARIO = 'dry-spell-rainfed'
/** The scenario's observed days (docs/06:465): the veranillo's 21 days, the
 * same length the spec hands `sim run --backfill` and the replay below. */
const SCENARIO_DAYS = 21
/** The loader's plot identity carries the scenario name (docs/04:263). */
const PLOT_NAME_PREFIX = 'Escenario dry-spell-rainfed'
/** decisionCopy.ts: the rainfed band always names the deficit (ADR-0023),
 * and `conserve_moisture` is the day's advice for a depletion past RAW — the
 * arithmetics #340 restored (the pilot's old assertion watched the broken
 * pipeline answer `no_action`). */
const RAINFED_BAND_PREFIX = 'Al cultivo le faltan'
const CONSERVE_ADVICE = 'Cubra el suelo con rastrojo para conservar la humedad.'

test('@pr dry-spell-rainfed: the full loop runs and the oracle passes', async ({ page }) => {
  await test.step('the stack answers', waitForApi)

  let phone = ''
  await test.step('sim run provisions the scenario (D-T16.5)', async () => {
    const provision = await runSimScenario(SCENARIO, '21d')
    phone = provision.phone
  })

  let token = ''
  let orgId = ''
  let scenarioPlotId = ''
  await test.step('the loader prepares base data + fixture (D-T16.2)', async () => {
    token = await login(phone)
    orgId = await firstOrgId(token)
    const loaded = await loadScenario(token, SCENARIO)
    scenarioPlotId = loaded.plot_ids[0] as string
  })

  await test.step('the irrigation jobs replay the scenario’s days', async () => {
    // The scenario's own observed window: 21 fixture days (docs/06:465-471).
    // One job per local day, oldest first — the balance accumulates one row
    // per day, which is what both the `conserve_moisture` advice and the
    // balance branch of `water_stress` read.
    await replayIrrigationDays(token, SCENARIO_DAYS)
  })

  await test.step('the rainfed recommendation appears (named poll)', async () => {
    await expect
      .poll(
        async () => (await plotStatus(token, scenarioPlotId)).recommendation?.kind ?? null,
        {
          timeout: E2E_TIMING.jobPollTimeoutMs,
          intervals: [E2E_TIMING.jobPollIntervalMs],
          message: 'the irrigation job did not store the day’s recommendation',
        },
      )
      .toBe('rainfed')
  })

  await test.step('the balance sweep runs through the real worker', async () => {
    // The balance branch of `water_stress` has no HTTP trigger: the runner
    // defers its 04:50 task and the running worker executes it. Whether it
    // opens the alert is the `expected` block's business — judged below, in
    // one place, by the checker.
    await deferBalanceSweep(scenarioPlotId)
  })

  await test.step('the T4 checker judges the expected block against live state', async () => {
    const verdict = await checkExpected({ scenario: SCENARIO, plotId: scenarioPlotId })
    // The oracle holds: the balance's rainfed branch opens `water_stress`
    // past RAW on day 16, the recommendation is `rainfed` and the advice is
    // `conserve_moisture` — the docs' arithmetic, restored by #340.
    expect(verdict.passed, JSON.stringify(verdict)).toBe(true)
    expect(verdict.findings).toEqual([])
  })

  await test.step('the PWA shows the decision the producer reads', async () => {
    // The org holds two plots (sim run's `Lote 1` + the loader's scenario
    // plot): the remembered active plot points the home screen at the
    // scenario's own, the way the plot listing would after the farmer taps it.
    rememberActivePlot(page, orgId, scenarioPlotId)
    await signIn(page, phone, PLOT_NAME_PREFIX)

    // Inicio item 2: the rainfed decision card renders the day's decision
    // from the recommendation the pipeline stored (ADR-0023's band + advice
    // line) — the machinery the scenario screens stand on.
    await expect(page.getByText(RAINFED_BAND_PREFIX).first()).toBeVisible()
    await expect(page.getByText(CONSERVE_ADVICE)).toBeVisible()
  })
})
