/**
 * Scenario C (`node-failure`, docs/06:473-480): a node goes down on day 4 and
 * stays down — the technician's `node_offline` opens and completeness observes
 * the gap. D-T16.13: node health decides on REAL arrival silence (`last_seen_at`,
 * margin 3 × `interval_s`), so this e2e is the one that pays it — C declares
 * `interval_s: 300` (a 15-minute margin) and the runner keeps node 1
 * publishing live while node 2 stays silent, the way the real farm looks. The
 * harness waits the real silence out by polling the node's own `/health`,
 * then defers the health sweep and the real worker opens the alert.
 *
 * The wait is nightly-only (D-T16.4's split): without
 * `E2E_SCENARIO_SILENCE_WAIT=1` the spec skips itself, and says why.
 */

import { expect, devices, test } from '@playwright/test'

import {
  E2E_TIMING,
  checkExpected,
  deferAlertsJob,
  firstOrgId,
  loadScenario,
  login,
  nodeHealth,
  plotStatus,
  runIrrigationJob,
  runSimScenario,
  startBackfill,
  waitForApi,
  type ScenarioLoad202,
} from './support/harness'
import { rememberActivePlot, signIn } from './support/pwa'

test.use({ ...devices['Pixel 7'] })

const SCENARIO = 'node-failure'
/** The scenario's observed days (the fixture's 14). */
const SCENARIO_DAYS = 14
/** The node row's cadence is the scenario's `interval_s: 300` (D-T16.13), and
 * node health's margin is 3 × it (alerts/domain NODE_SILENCE_INTERVALS). The
 * wait adds a margin of its own so the sweep's decision is unambiguous. */
const SILENCE_MARGIN_MS = 3 * 300 * 1_000
const SILENCE_EXTRA_MS = 45_000
/** The live tail outlives the wait: node 1 keeps reporting until the runner's
 * own deadline, after the sweep has already decided. */
const LIVE_SECONDS = 1_080
const PLOT_NAME_PREFIX = 'Escenario node-failure'
const NOT_NEEDED_BAND = 'Hoy no necesita riego'

let backgroundRun: { finished: Promise<unknown>; stop: () => void } | undefined

test.skip(
  process.env.E2E_SCENARIO_SILENCE_WAIT !== '1',
  'C pays a REAL 15-minute arrival silence (D-T16.13): nightly only, with E2E_SCENARIO_SILENCE_WAIT=1',
)

test('node-failure: the down node pages the technician and completeness observes the gap', async ({
  page,
}) => {
  test.setTimeout(E2E_TIMING.silencePollTimeoutMs + E2E_TIMING.jobPollTimeoutMs + 240_000)
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

  await test.step('the day’s irrigation job runs (node 1 keeps the balance alive)', async () => {
    await runIrrigationJob(token)
  })

  await test.step('the backfill is published; node 1 keeps publishing live (D-T16.11)', async () => {
    const run = startBackfill({ token, load: loaded, days: SCENARIO_DAYS, liveSeconds: LIVE_SECONDS })
    backgroundRun = run
  })

  await test.step('the failed node’s real silence is waited out (D-T16.13)', async () => {
    // The failed node is the second of the 202 — the fault names node 2, and
    // the loader claims in order. Its silence is measured on its own /health.
    const failedNodeId = nodeIds[1] as string
    await expect
      .poll(
        async () => {
          const health = await nodeHealth(token, failedNodeId)
          // A node that has not been seen yet has not gone silent yet: the
          // silence starts when its last reading lands, and the backfill is
          // still arriving. A node that never publishes at all is a different
          // scenario (a retired one), not this one — so "not seen yet" is zero
          // silence, not an infinite one.
          if (health.last_seen_at === null) return 0
          return Date.now() - Date.parse(health.last_seen_at)
        },
        {
          timeout: E2E_TIMING.silencePollTimeoutMs,
          intervals: [E2E_TIMING.silencePollIntervalMs],
          message: 'the failed node never stayed silent past the health margin',
        },
      )
      .toBeGreaterThan(SILENCE_MARGIN_MS + SILENCE_EXTRA_MS)
  })

  await test.step('completeness observes the gap, not a reconstruction', async () => {
    // The healthy node's completeness counts the readings of the last 24 h of
    // the clock (docs/11), and the backfill ends just before now — so the
    // number only exists once the ingestor has flushed the node's own day.
    // The wait is on that state, never on the clock (D-T16.4): the same rule
    // D-DH.15 applied to the escalation.
    await expect
      .poll(
        async () => (await nodeHealth(token, nodeIds[0] as string)).completeness_24h ?? 0,
        {
          timeout: E2E_TIMING.jobPollTimeoutMs,
          intervals: [E2E_TIMING.jobPollIntervalMs],
          message: "the healthy node's completeness never reached the backfilled day",
        },
      )
      .toBeGreaterThan(0.9)
    const failed = await nodeHealth(token, nodeIds[1] as string)
    expect(failed.completeness_24h).toBe(0)
  })

  await test.step('the health sweep runs through the real worker', async () => {
    await deferAlertsJob('alerts.evaluate_org_node_health', orgId)
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

test.afterEach(async () => {
  // The live tail has outlived its usefulness by now either way; stopping a
  // runner that already exited is a no-op.
  backgroundRun?.stop()
  await backgroundRun?.finished.catch(() => undefined)
  backgroundRun = undefined
})
