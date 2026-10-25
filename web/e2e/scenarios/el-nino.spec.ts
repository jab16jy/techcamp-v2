/**
 * Scenario A (`el-nino`, docs/06:442-448): El Niño water stress, end to end
 * through the broker. The documented loop (docs/06:408-419), compressed:
 * `sim run` provisions as the one command (its dev-OTP prompt fed through
 * stdin, D-T16.11), the loader prepares the scenario plot, the irrigation
 * jobs replay the 14 observed days (what the 04:30 cron does across the
 * scenario's real days — the balance rows the readings branch of
 * `water_stress` reads θ_estrés from must exist before the readings land),
 * and the backfill is published to the LOADER's nodes over MQTT. The
 * ingestor's batches open `heat_stress` and open-and-upgrade `water_stress`
 * to critical; the escalation sweep turns the critical into the technician's
 * SMS, which is what `GET /dev/outbox` shows (D-T16.6). The T4 checker judges
 * the `expected` block and its verdict is asserted green — D-T16.12 reads the
 * plot alerts on what the run opened (heat resolves nightly), D-T16.13 keeps
 * `node_offline` out of A's oracle.
 *
 * Every wait polls `expected` state with the named intervals of D-T16.4.
 * `@pr`: this scenario runs per PR (D-T16.4's split), nightly too.
 */

import { expect, devices, test } from '@playwright/test'

import {
  E2E_TIMING,
  checkExpected,
  deferAlertsJob,
  firstOrgId,
  loadScenario,
  login,
  plotStatus,
  publishBackfill,
  readOutbox,
  replayIrrigationDays,
  runSimScenario,
  waitForApi,
  type ScenarioLoad202,
} from './support/harness'
import { rememberActivePlot, signIn } from './support/pwa'

test.use({ ...devices['Pixel 7'] })

const SCENARIO = 'el-nino'
/** The scenario's observed days (docs/06:426): the fixture's 14 days, the
 * same length the spec hands `sim run --backfill`, the replay and the
 * loader-plot backfill. */
const SCENARIO_DAYS = 14
const PLOT_NAME_PREFIX = 'Escenario el-nino'
/** decisionCopy.ts: the irrigate card always names the depth ("Hoy: regar X
 * mm"); the plot's drip system gives it a duration too. */
const IRRIGATE_BAND_PREFIX = 'Hoy: regar'

test('@pr el-nino: water stress ends critical, the advice is to irrigate, the outbox carries the page', async ({
  page,
}) => {
  // The paced backfill alone is ~2 minutes of publish (2688 uplinks with a
  // 50 ms breath each); the whole loop fits well inside ten.
  test.setTimeout(600_000)
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

  await test.step('the irrigation jobs replay the scenario’s days', async () => {
    // One job per local day, oldest first: the balance accumulates one row
    // per day, and each row carries the θ_estrés the readings branch reads.
    await replayIrrigationDays(token, SCENARIO_DAYS)
  })

  await test.step('the last balance row is stored before any reading lands (named poll)', async () => {
    // The jobs run through the worker asynchronously, and the readings
    // branch of `water_stress` reads θ_estrés off the newest balance row:
    // the day's recommendation existing is the receipt that the replay
    // finished before the backfill starts.
    await expect
      .poll(
        async () => (await plotStatus(token, scenarioPlotId)).recommendation !== null,
        {
          timeout: E2E_TIMING.jobPollTimeoutMs,
          intervals: [E2E_TIMING.jobPollIntervalMs],
          message: 'the replayed irrigation jobs never stored the day’s recommendation',
        },
      )
      .toBe(true)
  })

  await test.step('the loader plot’s backfill is published over MQTT (D-T16.11)', async () => {
    // Paced: the ingestor decides per flush, and the reading branch's open and
    // the 48 h upgrade (D5) are measured at flush boundaries on the
    // trajectory's own clock — the pacing keeps those boundaries ~0.1
    // trajectory-days apart, the cadence a real node stream has.
    await publishBackfill({ token, load: loaded, days: SCENARIO_DAYS, tickDelayMs: 50 })
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
      .toBe('irrigate')
  })

  await test.step('the escalation runs through the real worker (D-T16.6)', async () => {
    // The critical's 2 h clock (docs/06 §3) is the escalation sweep's own
    // procrastinate task; the worker executes it and its SMS lands in the
    // tray the room reads.
    await deferAlertsJob('alerts.escalate_org_alerts', orgId)
  })

  await test.step('the outbox carries the critical page (named poll)', async () => {
    await expect
      .poll(
        async () =>
          (await readOutbox()).filter(
            (row) =>
              row.org_id === orgId && row.rule_code === 'water_stress' && row.severity === 'critical',
          ).length,
        {
          timeout: E2E_TIMING.jobPollTimeoutMs,
          intervals: [E2E_TIMING.jobPollIntervalMs],
          message: 'the escalation’s SMS never reached the outbox',
        },
      )
      .toBeGreaterThan(0)
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
    // Inicio item 2: the irrigate decision card renders the day's depth.
    await expect(page.getByText(IRRIGATE_BAND_PREFIX).first()).toBeVisible()
  })
})
