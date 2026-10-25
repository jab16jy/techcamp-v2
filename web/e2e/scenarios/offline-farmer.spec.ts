/**
 * Scenario D (`offline-farmer`, docs/06:473-480 row D) end to end. The server
 * side is deliberately unremarkable — one well-watered node, the moisture runs
 * above θ_estrés, nothing opens — so the T4 checker judges the empty
 * `expected` both ways and passes, and what the scenario really asserts is
 * E8's offline path (C5: assert, do not rebuild): the PWA in airplane mode
 * takes a logbook entry through the documented flow (docs/07:125, Bitácora →
 * Nueva entrada), and the sync E8 already guarantees uploads it with no
 * duplicates — the count is read from the server's own `GET /sync/pull`.
 *
 * Every wait polls `expected` state with the named intervals of D-T16.4.
 * Nightly only (D-T16.4's split: A+E per PR, all of them nightly) — D is
 * neither, so no `@pr` tag.
 */

import { expect, devices, test } from '@playwright/test'

import {
  E2E_TIMING,
  checkExpected,
  firstOrgId,
  liveLogbookEntries,
  loadScenario,
  login,
  plotName,
  plotStatus,
  publishBackfill,
  pullSyncPage,
  runIrrigationJob,
  runSimScenario,
  waitForApi,
  type ScenarioLoad202,
} from './support/harness'
import { rememberActivePlot, signIn } from './support/pwa'

test.use({ ...devices['Pixel 7'] })

const SCENARIO = 'offline-farmer'
/** The observed days the scenario runs: the same benign `normal-caribe`
 * fixture and length the `normal` contrast runs (docs/06:473-480). */
const SCENARIO_DAYS = 14
/** The loader's plot identity carries the scenario name (docs/04:263). */
const PLOT_NAME_PREFIX = 'Escenario offline-farmer'
/** The entry the farmer writes offline; the server row is asserted to carry it. */
const ENTRY_NOTES = 'Registro offline del escenario D'

test('offline-farmer: nothing opens, and the PWA syncs the offline entry without duplicates', async ({
  page,
}) => {
  test.setTimeout(E2E_TIMING.scenarioCommandTimeoutMs + 3 * E2E_TIMING.jobPollTimeoutMs)
  await test.step('the stack answers', waitForApi)

  let phone = ''
  await test.step('sim run provisions the scenario (D-T16.5, the one command)', async () => {
    const provision = await runSimScenario(SCENARIO, `${SCENARIO_DAYS}d`)
    phone = provision.phone
  })

  let token = ''
  let orgId = ''
  let scenarioPlotId = ''
  let loaded: ScenarioLoad202
  await test.step('the loader prepares base data + fixture (D-T16.2)', async () => {
    token = await login(phone)
    orgId = await firstOrgId(token)
    loaded = await loadScenario(token, SCENARIO)
    scenarioPlotId = loaded.plot_ids[0] as string
  })

  await test.step('the day’s irrigation job runs', async () => {
    await runIrrigationJob(token)
  })

  await test.step('the loader plot’s backfill is published over MQTT (D-T16.11)', async () => {
    // No pacing: nothing opens at ingest — the trajectory runs above
    // θ_estrés, which is what D's empty `expected` asserts.
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
      nodeIds: [...loaded.node_ids],
    })
    expect(verdict.passed, JSON.stringify(verdict)).toBe(true)
    expect(verdict.findings).toEqual([])
  })

  const scenarioPlotName = await plotName(token, orgId, scenarioPlotId)

  await test.step('the PWA takes a logbook entry in airplane mode (docs/07:125)', async () => {
    rememberActivePlot(page, orgId, scenarioPlotId)
    await signIn(page, phone, PLOT_NAME_PREFIX)
    await page.getByRole('link', { name: 'Bitácora' }).click()
    await expect(page.getByRole('heading', { name: 'Bitácora' })).toBeVisible()

    // Pre-warm the tab's farms/plots queries while online (D-T16.14 wiring):
    // the sheet below reads the tab's plots, and offline nothing may fetch.
    await page.getByRole('button', { name: 'Nueva entrada' }).first().click()
    await expect(page.getByLabel('Parcela')).toContainText(scenarioPlotName)
    await page.getByRole('button', { name: 'Cancelar' }).click()

    await page.context().setOffline(true)

    await page.getByRole('button', { name: 'Nueva entrada' }).first().click()
    await page.getByLabel('Parcela').selectOption({ label: scenarioPlotName })
    await page.getByLabel('Tipo de registro').selectOption({ label: 'Observación' })
    await page.getByLabel('Observación (opcional)').fill(ENTRY_NOTES)
    await page.getByRole('button', { name: 'Guardar' }).click()
    // The offline save's own honesty: the phone says where the entry lives.
    await expect(page.getByText('Guardado en el teléfono').first()).toBeVisible()
  })

  await test.step('back online, the synchronizer uploads the entry and the UI says so', async () => {
    await page.context().setOffline(false)
    await page.reload()
    await page.getByRole('link', { name: 'Bitácora' }).click()
    await expect(page.getByText('Sincronizado').first()).toBeVisible({
      timeout: E2E_TIMING.jobPollTimeoutMs,
    })
  })

  await test.step('the server holds exactly one row, the one the phone wrote offline', async () => {
    // The count is the server's own pull (docs/04 §Bitácora), never the phone's
    // Dexie — the "sin duplicados" half of row D is a server-side fact.
    await expect
      .poll(async () => liveLogbookEntries(await pullSyncPage(token), scenarioPlotId).length, {
        timeout: E2E_TIMING.jobPollTimeoutMs,
        intervals: [E2E_TIMING.jobPollIntervalMs],
        message: 'the offline entry did not reach the server (or reached it twice)',
      })
      .toBe(1)
    const [entry] = liveLogbookEntries(await pullSyncPage(token), scenarioPlotId)
    expect(entry?.created_offline, 'the entry must be marked created_offline').toBe(true)
    expect(entry?.notes).toBe(ENTRY_NOTES)
    expect(entry?.kind).toBe('observation')
  })

  await test.step('a second sync run adds no second row (E8’s idempotent push)', async () => {
    // A reload starts a fresh synchronizer run: push finds an empty outbox and
    // pull replays the entry — the count stays one, which is the duplicate
    // guarantee docs/04 §Bitácora's `duplicate` answer buys.
    await page.reload()
    await expect
      .poll(async () => liveLogbookEntries(await pullSyncPage(token), scenarioPlotId).length, {
        timeout: E2E_TIMING.jobPollTimeoutMs,
        intervals: [E2E_TIMING.jobPollIntervalMs],
        message: 'the second sync run duplicated the entry',
      })
      .toBe(1)
  })
})
