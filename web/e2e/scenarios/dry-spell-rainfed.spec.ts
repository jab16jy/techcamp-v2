/**
 * E16 T5 pilot: scenario E (`dry-spell-rainfed`, docs/06:459-471) end to end.
 *
 * E is the one scenario the merged stack runs without a broker: no nodes, so
 * the loader (T3) prepares base data + fixture, the irrigation jobs replay the
 * scenario's 21 days (what the 04:30 cron does across the scenario's real
 * days), and the balance sweep runs through the real worker. The T4 checker
 * judges the `expected` block and its verdict is asserted here — pinned, on
 * this recorded pipeline, to the finding the feature doc records: the crop
 * catalog's maize stage lengths do not reproduce the docs' arithmetic, so the
 * oracle answers exactly `missing_alert(water_stress)`. T6's wiring turns the
 * verdict green once the owner's catalog/scenario decision lands.
 *
 * Every wait polls `expected` state with the named intervals of D-T16.4.
 */

import { expect, devices, test } from '@playwright/test'

import {
  E2E_TIMING,
  apiLogOffset,
  checkExpected,
  deferBalanceSweep,
  firstOrgId,
  loadScenario,
  login,
  plotStatus,
  readOtpCodeAfter,
  replayIrrigationDays,
  runSimScenario,
  waitForApi,
  type ExpectedVerdictDto,
} from './support/harness'

test.use({ ...devices['Pixel 7'] })

const SCENARIO = 'dry-spell-rainfed'
/** The scenario's observed days (docs/06:465): the veranillo's 21 days, the
 * same length the spec hands `sim run --backfill` and the replay below. */
const SCENARIO_DAYS = 21
/** The loader's plot identity carries the scenario name (docs/04:263). */
const PLOT_NAME_PREFIX = 'Escenario dry-spell-rainfed'
/** decisionCopy.ts: the rainfed band always names the deficit (ADR-0023),
 * and `no_action` is the day's advice sentence for a depletion below RAW. */
const RAINFED_BAND_PREFIX = 'Al cultivo le faltan'
const NO_ACTION_ADVICE = 'Por hoy no hace falta ninguna acción en esta parcela.'

test(`${SCENARIO}: the full loop runs and the checker names the recorded pipeline's gap`, async ({
  page,
}) => {
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
    // The recommendation half of the grammar holds on the recorded pipeline:
    // the balance ran rainfed with `K = 0` and no unexpected kind appeared.
    expect(
      verdict.findings.filter((finding) => finding.kind === 'irrigation_kind'),
      JSON.stringify(verdict),
    ).toEqual([])
    // BLOCKED ON THE OWNER (the T5 finding, feature doc "Review (RDD)"): the
    // crop catalog's maize `mid` stage is 31 days (90-day total) while
    // docs/06:465-471 cites FAO-56 Tables 12/22 (mid = 40, total 130), so a
    // cycle sown 60 days before the anchor enters the `late` stage (Kc 0.35)
    // on window day 16, ETc collapses below RAW's own adjustment, and the
    // day-16 crossing of RAW never sustains. The recorded pipeline answers
    // exactly these three findings — the oracle detects the gap the docs'
    // arithmetic does not reproduce, and names it the way the YAML reads.
    // When the owner's catalog/scenario decision lands, T6's wiring turns
    // this into `verdict.passed === true`.
    expect(verdict.passed).toBe(false)
    expect(verdict.findings).toEqual([
      { kind: 'missing_alert', expected: 'water_stress', observed: 'no alert open' },
      { kind: 'missing_advice', expected: 'conserve_moisture', observed: 'no_action' },
      { kind: 'unexpected_advice', expected: 'conserve_moisture', observed: 'no_action' },
    ] satisfies ExpectedVerdictDto['findings'])
  })

  await test.step('the PWA shows the decision the producer reads', async () => {
    // The org holds two plots (sim run's `Lote 1` + the loader's scenario
    // plot): the remembered active plot points the home screen at the
    // scenario's own, the way the plot listing would after the farmer taps it.
    await page.addInitScript(
      ({ orgId, plotId }) => {
        localStorage.setItem('techcamp.active_plot', JSON.stringify({ [orgId]: plotId }))
      },
      { orgId, plotId: scenarioPlotId },
    )
    await page.goto('/')

    // The sign-in the PWA owns: phone → code → (single org) → Inicio.
    await page.getByLabel('Teléfono').fill(phone)
    const otpBefore = await apiLogOffset()
    await page.getByRole('button', { name: 'Enviar código' }).click()
    const code = await readOtpCodeAfter(phone, otpBefore)
    await page.getByLabel('Código').fill(code)
    await page.getByRole('button', { name: 'Verificar' }).click()

    // The scenario plot's own name is the screen's identity.
    await expect(page.getByText(PLOT_NAME_PREFIX).first()).toBeVisible()
    // Inicio item 2: the rainfed decision card renders the day's decision
    // from the recommendation the pipeline stored (ADR-0023's band + advice
    // line) — the machinery the scenario screens stand on.
    await expect(page.getByText(RAINFED_BAND_PREFIX).first()).toBeVisible()
    await expect(page.getByText(NO_ACTION_ADVICE)).toBeVisible()
  })
})
