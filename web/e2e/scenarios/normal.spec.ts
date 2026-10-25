/**
 * The contrast scenario (`normal`, docs/06:473-480): stable operation,
 * nothing opens. Its `expected` block is empty on purpose — the checker's
 * exact match both ways turns that silence into the assertion that the
 * pipeline the other scenarios drive opens NOTHING on a well-kept plot.
 *
 * Every wait polls `expected` state with the named intervals of D-T16.4.
 * Nightly only (D-T16.4's split: A+E per PR, all of them nightly).
 */

import { expect, devices, test } from '@playwright/test'

import {
  E2E_TIMING,
  checkExpected,
  firstOrgId,
  loadScenario,
  login,
  plotStatus,
  publishBackfill,
  runIrrigationJob,
  runSimScenario,
  waitForApi,
  type ScenarioLoad202,
} from './support/harness'
import { rememberActivePlot, signIn } from './support/pwa'

test.use({ ...devices['Pixel 7'] })

const SCENARIO = 'normal'
/** The scenario's observed days (the fixture's 14). */
const SCENARIO_DAYS = 14
const PLOT_NAME_PREFIX = 'Escenario normal'
const NOT_NEEDED_BAND = 'Hoy no necesita riego'

test('normal: stable operation, nothing opens, the day needs no irrigation', async ({ page }) => {
  test.setTimeout(E2E_TIMING.scenarioCommandTimeoutMs + E2E_TIMING.jobPollTimeoutMs)
  await test.step('the stack answers', waitForApi)

  let phone = ''
  await test.step('sim run provisions the scenario (D-T16.5, the one command)', async () => {
    const provision = await runSimScenario(SCENARIO, `${SCENARIO_DAYS}d`)
    phone = provision.phone
  })

  let token = ''
  let orgId = ''
  let scenarioPlotId = ''
  let nodeIds: string[] = []
  let loaded: ScenarioLoad202
  await test.step('the loader prepares base data + fixture (D-T16.2)', async () => {
    token = await login(phone)
    orgId = await firstOrgId(token)
    loaded = await loadScenario(token, SCENARIO)
    scenarioPlotId = loaded.plot_ids[0] as string
    nodeIds = [...loaded.node_ids]
  })

  await test.step('the day’s irrigation job runs', async () => {
    await runIrrigationJob(token)
  })

  await test.step('the loader plot’s backfill is published over MQTT (D-T16.11)', async () => {
    await publishBackfill({ token, load: loaded, days: SCENARIO_DAYS })
  })

  await test.step('the recommendation appears (named poll)', async () => {
    await expect
      .poll(
        async () => (await plotStatus(token, scenarioPlotId)).recommendation?.kind ?? null,
        {
          timeout: E2E_TIMING.jobPollTimeoutMs,
          intervals: [E2E_TIMING.jobPollIntervalMs],
          message: 'the irrigation job did not store the day’s recommendation',
        },
      )
      .toBe('not_needed')
  })

  await test.step('the T4 checker judges the expected block against live state', async () => {
    const verdict = await checkExpected({
      scenario: SCENARIO,
      plotId: scenarioPlotId,
      nodeIds,
    })
    expect(verdict.passed, JSON.stringify(verdict)).toBe(true)
    expect(verdict.findings).toEqual([])
  })

  await test.step('the PWA shows the decision the producer reads', async () => {
    rememberActivePlot(page, orgId, scenarioPlotId)
    await signIn(page, phone, PLOT_NAME_PREFIX)
    await expect(page.getByText(NOT_NEEDED_BAND).first()).toBeVisible()
  })
})
