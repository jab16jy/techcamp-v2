/**
 * The scenario harness's orchestration side (docs/06-diseno-detallado.md:485,
 * D-T16.4). The Playwright specs drive the PWA; this module drives everything
 * around them: the simulator CLI, the `/dev` routes, the procrastinate sweep
 * the docs give no HTTP route, and the T4 `expected` checker runner.
 *
 * Every wait is a named constant in `E2E_TIMING` — D-T16.4's "timeouts e
 * intervalos de poll nombrados" — and the polls wait on `expected` state,
 * never on wall-clock arithmetic (techcamp-v2-e16-blockers.md D2).
 *
 * Pure of Playwright on purpose: the parsers are unit-tested by Vitest and
 * everything here is plain Node the specs call.
 */

import { execFile, spawn } from 'node:child_process'
import { readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

/** D-T16.4: the poll interval and timeout every wait in the harness uses.
 * Numbers, never ambient sleeps: a scenario's wait is a poll on state. */
export const E2E_TIMING = {
  apiReadyTimeoutMs: 60_000,
  apiReadyIntervalMs: 1_000,
  scenarioCommandTimeoutMs: 240_000,
  jobPollTimeoutMs: 180_000,
  jobPollIntervalMs: 2_000,
  otpLogTimeoutMs: 30_000,
  otpLogIntervalMs: 500,
  silencePollTimeoutMs: 1_200_000,
  silencePollIntervalMs: 15_000,
} as const

export interface E2eConfig {
  /** The api's `/api/v1` root the harness talks to directly. */
  apiUrl: string
  /** The web origin the browser under test opens. */
  webUrl: string
  /** Where the api process's stdout goes: the dev OTP is printed there
   * (`[dev] OTP for <phone>: <code>`, docs/04-api.md:173), and the harness
   * reads it — there is no endpoint that returns the code. */
  apiLogPath: string
  /** The server checkout, so `uv run` finds the techcamp package. */
  serverDir: string
}

export function e2eConfig(): E2eConfig {
  const supportDir = dirname(fileURLToPath(import.meta.url))
  const webDir = resolve(supportDir, '..', '..', '..')
  return {
    apiUrl: process.env.E2E_API_URL ?? 'http://localhost:8000/api/v1',
    webUrl: process.env.E2E_WEB_URL ?? 'http://localhost:5173',
    apiLogPath: process.env.E2E_API_LOG ?? join(process.env.TMPDIR ?? '/tmp', 'techcamp-e2e-api.log'),
    serverDir: process.env.E2E_SERVER_DIR ?? resolve(webDir, '..', 'server'),
  }
}

// -- The two console formats the stack prints for scripted runs ----------------

/** The code the api printed for `phone` in `log`, or null while it has not:
 * the newest line wins, because `otp_store.issue` overwrites the previous
 * code and only the newest one still verifies. */
export function parseOtpLine(log: string, phone: string): string | null {
  const lines = log.split('\n')
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const match = lines[i].match(/^\[dev\] OTP for (.+?): (\d{6})$/)
    if (match !== null && match[1] === phone) return match[2]
  }
  return null
}

export interface SimRunProvision {
  /** The member phone `provision_scenario` created for this run: the identity
   * both the harness and the browser log in with. */
  phone: string
  plotId: string
}

/** What `sim run` printed about the scenario it provisioned, or null when it
 * failed before provisioning (the caller surfaces stderr). */
export function parseSimRunOutput(output: string): SimRunProvision | null {
  const match = output.match(
    /^\[sim\] scenario (\S+): member (\S+), plot ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/m,
  )
  if (match === null) return null
  return { phone: match[2], plotId: match[3] }
}

// -- api calls -----------------------------------------------------------------

async function apiFetch(
  path: string,
  init: RequestInit & { token?: string } = {},
): Promise<Response> {
  const { apiUrl, apiLogPath } = e2eConfig()
  const headers: Record<string, string> = { 'content-type': 'application/json' }
  if (init.token !== undefined) headers.authorization = `Bearer ${init.token}`
  const response = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers as Record<string, string>) },
  })
  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`${init.method ?? 'GET'} ${path} -> ${response.status}: ${body.slice(0, 500)} (api log: ${apiLogPath})`)
  }
  return response
}

export async function waitForApi(): Promise<void> {
  const { apiUrl } = e2eConfig()
  // The unversioned liveness probe (main.py: `/health`) — the harness only
  // needs "the process is up"; readiness belongs to the polls on state.
  const healthUrl = `${apiUrl.replace(/\/api\/v1\/?$/, '')}/health`
  const deadline = Date.now() + E2E_TIMING.apiReadyTimeoutMs
  for (;;) {
    try {
      const response = await fetch(healthUrl)
      if (response.ok) return
    } catch {
      // the api is not listening yet: poll again
    }
    if (Date.now() > deadline) {
      throw new Error(`the api did not answer ${healthUrl} within ${E2E_TIMING.apiReadyTimeoutMs}ms`)
    }
    await new Promise((resolveSleep) => setTimeout(resolveSleep, E2E_TIMING.apiReadyIntervalMs))
  }
}

async function logSizeOrZero(path: string): Promise<number> {
  try {
    return statSync(path).size
  } catch {
    return 0
  }
}

/** One dev OTP round: request the code and read it off the api log, polling
 * because the print lands a moment after the 204. Only bytes appended after
 * the request are read, so a code an earlier round printed for the same phone
 * is never handed out again. */
export async function requestOtpCode(phone: string): Promise<string> {
  const fromOffset = await apiLogOffset()
  await apiFetch('/dev/auth/otp', { method: 'POST', body: JSON.stringify({ phone }) })
  return readOtpCodeAfter(phone, fromOffset)
}

/** The api log's current size: the offset a caller captures before the PWA
 * requests its own code, so the read below sees only that round's print. */
export async function apiLogOffset(): Promise<number> {
  return logSizeOrZero(e2eConfig().apiLogPath)
}

/** Poll the api log for the code printed after `fromOffset` for `phone` — the
 * read-only half of `requestOtpCode`, for the round the PWA itself requested. */
export async function readOtpCodeAfter(phone: string, fromOffset: number): Promise<string> {
  const { apiLogPath } = e2eConfig()
  const deadline = Date.now() + E2E_TIMING.otpLogTimeoutMs
  for (;;) {
    let appended = ''
    try {
      appended = readFileSync(apiLogPath, 'utf8').slice(fromOffset)
    } catch {
      // the log file may not exist yet: poll again
    }
    const code = parseOtpLine(appended, phone)
    if (code !== null) return code
    if (Date.now() > deadline) {
      throw new Error(`the api log printed no OTP for ${phone} within ${E2E_TIMING.otpLogTimeoutMs}ms (log: ${apiLogPath})`)
    }
    await new Promise((resolveSleep) => setTimeout(resolveSleep, E2E_TIMING.otpLogIntervalMs))
  }
}

/** A harness-side session: the same dev OTP flow the PWA's sign-in drives. */
export async function login(phone: string): Promise<string> {
  const code = await requestOtpCode(phone)
  const response = await apiFetch('/dev/auth/otp/verify', {
    method: 'POST',
    body: JSON.stringify({ phone, code }),
  })
  const body = (await response.json()) as { access_token: string }
  return body.access_token
}

/** The caller's own organization: the loader resolves the scenario into the
 * one organization the member belongs to (the simulator created it). */
export async function firstOrgId(token: string): Promise<string> {
  const response = await apiFetch('/me', { token })
  const body = (await response.json()) as { memberships: Array<{ org_id: string }> }
  const membership = body.memberships[0]
  if (membership === undefined) throw new Error('the scenario member belongs to no organization')
  return membership.org_id
}

export interface ScenarioNodeCredential {
  node_id: string
  mqtt_username: string
  mqtt_password: string
}

export interface ScenarioLoad202 {
  scenario: string
  plot_ids: string[]
  node_ids: string[]
  cycle_ids: string[]
  node_credentials: ScenarioNodeCredential[]
}

/** `POST /dev/scenarios/{name}:load → 202` (docs/04-api.md:263): base data +
 * the weather fixture, idempotent per (scenario, day-anchor). */
export async function loadScenario(
  token: string,
  name: string,
  opts: { day?: string; replace?: boolean } = {},
): Promise<ScenarioLoad202> {
  const response = await apiFetch(`/dev/scenarios/${name}:load`, {
    method: 'POST',
    token,
    body: JSON.stringify(opts),
  })
  return (await response.json()) as ScenarioLoad202
}

/** `POST /dev/jobs/irrigation:run` — the 04:30 balance and recommendation,
 * for the day the caller names (docs/04-api.md:181); no day is the server's
 * own local today. */
export async function runIrrigationJob(token: string, day?: string): Promise<number> {
  const response = await apiFetch('/dev/jobs/irrigation:run', {
    method: 'POST',
    token,
    body: JSON.stringify(day === undefined ? {} : { day }),
  })
  const body = (await response.json()) as { job_id: number }
  return body.job_id
}

/** The calendar day an instant falls on in America/Bogota, `YYYY-MM-DD` — the
 * zone every job hour and every date default in the product uses
 * (techcamp/shared/dates.py). Pure, so the boundary case is unit-tested. */
export function bogotaDateAt(instant: Date): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Bogota',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(instant)
}

function datePlusDays(isoDate: string, days: number): string {
  const [year, month, day] = isoDate.split('-').map(Number) as [number, number, number]
  // Noon UTC keeps the calendar arithmetic away from any zone's midnight.
  const shifted = new Date(Date.UTC(year, month - 1, day + days, 12))
  return shifted.toISOString().slice(0, 10)
}

/** One `/dev/jobs/irrigation:run` per day for the last `days` local days,
 * oldest first: the balance accumulates one row per day (Dr grows by the day),
 * and a single run would leave the scenario's `expected` reading a one-day
 * depletion. This replay is what the 04:30 cron does across the scenario's
 * real days; the e2e compresses it into seconds. */
export async function replayIrrigationDays(token: string, days: number): Promise<void> {
  const today = bogotaDateAt(new Date())
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    await runIrrigationJob(token, datePlusDays(today, -offset))
  }
}

export interface PlotStatusSummary {
  recommendation: { kind: string } | null
  open_alerts: Array<{ rule_code: string }>
}

/** `GET /plots/{plot_id}/status` — the same payload the PWA's home renders. */
export async function plotStatus(token: string, plotId: string): Promise<PlotStatusSummary> {
  const response = await apiFetch(`/plots/${plotId}/status`, { token })
  const body = (await response.json()) as PlotStatusSummary
  return {
    recommendation: body.recommendation === null ? null : { kind: body.recommendation.kind },
    open_alerts: body.open_alerts.map((alert) => ({ rule_code: alert.rule_code })),
  }
}

/** The scenario plot's own name — the loader's "Escenario <name> <ancla>"
 * identity (docs/04:263) — read the way the PWA reads it (farms, then each
 * farm's plots), because the Nueva entrada sheet's Parcela select takes the
 * option's label, not its id. */
export async function plotName(token: string, orgId: string, plotId: string): Promise<string> {
  const farmsResponse = await apiFetch(`/farms?org_id=${orgId}`, { token })
  const farms = (await farmsResponse.json()) as { items: Array<{ id: string }> }
  for (const farm of farms.items) {
    const plotsResponse = await apiFetch(`/farms/${farm.id}/plots`, { token })
    const plots = (await plotsResponse.json()) as Array<{ id: string; name: string }>
    const plot = plots.find((candidate) => candidate.id === plotId)
    if (plot !== undefined) return plot.name
  }
  throw new Error(`plot ${plotId} is not in any farm of org ${orgId}`)
}

/** `GET /nodes/{node_id}/health` (docs/04-api.md:100): the node's arrival
 * silence and its 24 h completeness — the surface scenario C's e2e observes
 * the gap on, and the poll that waits a real silence out (D-T16.13). */
export interface NodeHealthView {
  node_id: string
  last_seen_at: string | null
  completeness_24h: number | null
}

export async function nodeHealth(token: string, nodeId: string): Promise<NodeHealthView> {
  const response = await apiFetch(`/nodes/${nodeId}/health`, { token })
  return (await response.json()) as NodeHealthView
}

/** `GET /dev/outbox` (docs/04-api.md:264): the seminar tray of simulated
 * SMS/WhatsApp rows — one room, no session, no org filter. Scenario A's push
 * is asserted here (D-T16.6): the escalation's row names the rule and the
 * severity that produced it. */
export interface OutboxMessage {
  id: string
  alert_id: string
  org_id: string
  channel: string
  status: string
  rule_code: string
  severity: string
  created_at: string
}

export async function readOutbox(): Promise<OutboxMessage[]> {
  const response = await apiFetch('/dev/outbox')
  return (await response.json()) as OutboxMessage[]
}

// -- the E8 sync surface (docs/04 §Bitácora) ------------------------------------

/** The `data` of a pulled logbook-entry change (docs/03): the fields the
 * scenario D assertion reads, typed loosely where the count does not care. */
export interface SyncPullEntryData {
  id: string
  org_id: string
  plot_id: string
  kind: string
  notes: string | null
  created_offline: boolean
  client_updated_at: string
  deleted_at: string | null
}

export interface SyncPullEntryChange {
  id: string
  entity: 'logbook_entry'
  op: string
  server_version: number
  data: SyncPullEntryData
}

/** A pulled visit change: the count does not read its payload. */
export interface SyncPullVisitChange {
  id: string
  entity: 'extension_visit'
  op: string
  server_version: number
  data: Record<string, unknown>
}

export type SyncPullChange = SyncPullEntryChange | SyncPullVisitChange

/** `GET /sync/pull?since=&limit=` (docs/04 §Bitácora) — the page the PWA's own
 * synchronizer applies, read here from the harness so the "syncs with no
 * duplicates" assertion counts the server's rows, not the phone's. */
export interface SyncPullPage {
  changes: SyncPullChange[]
  next_since: number
  has_more: boolean
}

export async function pullSyncPage(token: string): Promise<SyncPullPage> {
  const response = await apiFetch('/sync/pull?since=0&limit=500', { token })
  return (await response.json()) as SyncPullPage
}

/** The live logbook entries one pull page carries for `plotId`: not another
 * entity, not tombstoned. A page listing two changes for two ids on this plot
 * is exactly the duplication scenario D must fail on, so the count is honest
 * and nothing is deduplicated here. */
export function liveLogbookEntries(page: SyncPullPage, plotId: string): SyncPullEntryData[] {
  return page.changes
    .filter((change) => change.entity === 'logbook_entry')
    .map((change) => change.data)
    .filter((data) => data.plot_id === plotId && data.deleted_at === null)
}

// -- the python side of the harness ---------------------------------------------

async function runServerPython(args: string[]): Promise<string> {
  const { serverDir } = e2eConfig()
  try {
    const { stdout } = await execFileAsync('uv', ['run', 'python', ...args], {
      cwd: serverDir,
      timeout: E2E_TIMING.scenarioCommandTimeoutMs,
      env: process.env,
      encoding: 'utf8',
    })
    return stdout
  } catch (error) {
    const cause = error as { stderr?: string; stdout?: string }
    throw new Error(
      `uv run python ${args[0]} failed: ${(cause.stderr ?? cause.stdout ?? String(error)).slice(0, 1_000)}`,
      { cause: error },
    )
  }
}

function supportScript(name: string): string {
  return join(dirname(fileURLToPath(import.meta.url)), name)
}

export interface ExpectedFindingDto {
  kind: string
  expected: string
  observed: string
}

export interface ExpectedVerdictDto {
  scenario: string
  passed: boolean
  findings: ExpectedFindingDto[]
}

/** `sim run --scenario <name> --backfill <dur>` (docs/06:408): the one-command
 * provisioning the acceptance criterion names, driven as the command itself.
 * The process provisions (D-T16.5), prints its provisioning line, requests the
 * dev OTP for the member it created and blocks on stdin for the code — no
 * endpoint returns it (docs/04-api.md:173) — so the harness feeds it: it polls
 * the api log for the code of the phone the provisioning line names and writes
 * it to the process's stdin, reading only bytes appended after its own start.
 * A node-less scenario exits before the login and never asks. */
export async function runSimScenario(name: string, backfill: string): Promise<SimRunProvision> {
  const { serverDir } = e2eConfig()
  const fromOffset = await apiLogOffset()
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(
      'uv',
      [
        'run',
        'python',
        '-m',
        'techcamp.simulator',
        'run',
        '--scenario',
        name,
        '--backfill',
        backfill,
        '--seed',
        '0',
      ],
      { cwd: serverDir, env: process.env, stdio: ['pipe', 'pipe', 'pipe'] },
    )
    let stdout = ''
    let stderr = ''
    let provision: SimRunProvision | null = null
    let fed = false
    const killTimer = setTimeout(() => child.kill('SIGKILL'), E2E_TIMING.scenarioCommandTimeoutMs)
    const fail = (message: string) => {
      clearTimeout(killTimer)
      rejectRun(
        new Error(
          `${message}. Output was:\n${(stdout + '\n' + stderr).slice(-1_000)}`,
        ),
      )
    }
    child.stdout.on('data', (chunk: Buffer) => {
      stdout += chunk.toString()
      provision = provision ?? parseSimRunOutput(stdout)
      if (provision !== null && !fed) {
        // sim run prints the provisioning line before it requests the code and
        // blocks on stdin; feeding once, on the code the api printed after this
        // run started, is the whole handshake.
        fed = true
        readOtpCodeAfter(provision.phone, fromOffset)
          .then((code) => {
            child.stdin?.write(`${code}\n`)
          })
          .catch((error: unknown) => fail(String(error)))
      }
    })
    child.stderr.on('data', (chunk: Buffer) => {
      stderr += chunk.toString()
    })
    child.on('error', (error) => fail(`sim run could not start: ${String(error)}`))
    child.on('close', (exitCode) => {
      clearTimeout(killTimer)
      const parsed = parseSimRunOutput(stdout)
      if (parsed === null) {
        fail(`sim run printed no provisioning line (exit ${exitCode})`)
        return
      }
      resolveRun(parsed)
    })
  })
}

/** D-T16.11: the loader's nodes get their calibrations and the scenario's
 * backfill through the support runner — `valid_from` covering the retroactive
 * window and one MQTT connection per node, inside the runner. The 202 travels
 * as an argument: it is a handful of ids and credentials. `liveSeconds` keeps
 * the runner publishing live after the backfill (docs/06:408's `--live`,
 * bounded) — the caller runs it in the background with `startBackfill` while
 * it waits on state. */
export interface BackfillArgs {
  token: string
  load: ScenarioLoad202
  days: number
  seed?: number
  liveSeconds?: number
  /** Delay per published uplink (ms): the pacing that keeps the ingestor's
   * flush boundaries fine-grained against the trajectory — the D5 upgrade
   * measures 48 h of it (the compression repair the A spec needs). */
  tickDelayMs?: number
}

function backfillRunnerArgs(args: BackfillArgs): string[] {
  const base = [
    supportScript('publish_backfill.py'),
    '--token',
    args.token,
    '--load-json',
    JSON.stringify(args.load),
    '--days',
    String(args.days),
    '--seed',
    String(args.seed ?? 0),
  ]
  if (args.liveSeconds !== undefined) base.push('--live-seconds', String(args.liveSeconds))
  if (args.tickDelayMs !== undefined) base.push('--tick-delay-ms', String(args.tickDelayMs))
  return base
}

export interface BackfillRun {
  finished: Promise<{ ticks: number; nodes: number }>
  /** Stop the runner (SIGKILL after the graceful timeout): a background live
   * tail the caller is done with ends here, not at its own deadline. */
  stop: () => void
}

export function startBackfill(args: BackfillArgs): BackfillRun {
  const { serverDir } = e2eConfig()
  // No execFile timeout: a live tail outlives `scenarioCommandTimeoutMs` by
  // design (D-T16.13's real silence), and the caller's await on `finished` —
  // inside the spec's own Playwright timeout — is what bounds it.
  const child = execFile(
    'uv',
    ['run', 'python', ...backfillRunnerArgs(args)],
    { cwd: serverDir, maxBuffer: 16 * 1024 * 1024 },
  )
  let stdout = ''
  child.stdout?.on('data', (chunk: Buffer) => {
    stdout += chunk.toString()
  })
  const finished = new Promise<{ ticks: number; nodes: number }>((resolve, reject) => {
    child.on('close', (code, signal) => {
      if (code !== 0) {
        reject(new Error(`publish_backfill.py exited ${code} (${signal}): ${stdout.slice(-500)}`))
        return
      }
      try {
        resolve(JSON.parse(stdout.trim().split('\n').at(-1) as string) as {
          ticks: number
          nodes: number
        })
      } catch (error) {
        reject(error as Error)
      }
    })
    child.on('error', reject)
  })
  return { finished, stop: () => child.kill() }
}

export async function publishBackfill(args: BackfillArgs): Promise<{ ticks: number; nodes: number }> {
  const stdout = await runServerPython(backfillRunnerArgs(args))
  return JSON.parse(stdout.trim().split('\n').at(-1) as string) as { ticks: number; nodes: number }
}

/** The three per-org alerts jobs no HTTP route triggers (D-T16.11's runner):
 * `alerts.evaluate_org_node_health` (node_offline), `alerts.evaluate_org_fungal_risk`
 * (its own 04:45 periodic) and `alerts.escalate_org_alerts` (a critical's 2 h
 * clock). The running worker — not the harness — executes them. */
export async function deferAlertsJob(task: string, orgId: string): Promise<number> {
  const stdout = await runServerPython([
    supportScript('defer_alerts_job.py'),
    '--task',
    task,
    '--org-id',
    orgId,
  ])
  return JSON.parse(stdout.trim().split('\n').at(-1) as string).job_id as number
}

/** The T4 checker, judged against live state: its own docstring names this
 * runner the harness prints (`ExpectedVerdict.as_dict`, JSON-safe). */
export async function checkExpected(args: {
  scenario: string
  plotId: string
  nodeIds?: string[]
}): Promise<ExpectedVerdictDto> {
  const scriptArgs = [
    supportScript('check_expected.py'),
    '--scenario',
    args.scenario,
    '--plot-id',
    args.plotId,
  ]
  if (args.nodeIds !== undefined && args.nodeIds.length > 0) {
    scriptArgs.push('--node-ids', args.nodeIds.join(','))
  }
  const stdout = await runServerPython(scriptArgs)
  return JSON.parse(stdout.trim().split('\n').at(-1) as string) as ExpectedVerdictDto
}

/** The balance branch of `water_stress` has no HTTP trigger: it is the 04:50
 * sweep's own procrastinate task (docs/06 §3, docs/10 §3). The runner defers
 * that same task, so the running worker — not the harness — executes it. */
export async function deferBalanceSweep(plotId: string): Promise<number> {
  const stdout = await runServerPython([supportScript('defer_balance_sweep.py'), '--plot-id', plotId])
  const last = stdout.trim().split('\n').at(-1) as string
  return JSON.parse(last).job_id as number
}
