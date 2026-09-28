# TechCamp v2 — E7 Alerts and notifications

## Objective
Deliver epic E7 from `docs/10-dag.md:70`: scenario A fires an alert and a push arrives (and the
simulated SMS lands in `/dev/outbox`); retries and escalation are tested. `flood_risk` /
`drought_risk` are seeded but only activate when E10 lands. Modules `alerts` and
`notifications` (docs/05 §Módulos: `alerts → telemetry, weather, risk, notifications`).

## Why
RF-07 (rule and model alerts, open → acknowledged → resolved) and RF-08 (web push, SMS/WhatsApp
fallback for critical) (docs/01:30-31). RNF-05: critical alert p95 < 2 min from reading to
notification (seminar: `/dev/outbox` or push). E7 unblocks E9 (home + technician tray with open
alerts), E11 (adoption index `risk_management`), E12 (assistant) and E16 (scenario `expected`
alerts).

## Scope
- In (server; web only for the push client):
  - Tables (docs/03:272-311, indexes docs/03:484-486): `alert_rule` (factory rules with
    `org_id = null`, seeded by migration), `alert` (partial unique index: one non-resolved alert
    per `rule_id` + `plot_id`/`node_id`, docs/06 §3), `notification`, `push_subscription`.
  - Alert domain (docs/06 §3): sustained condition over `min_duration_min`, hysteresis, the state
    machine (open, acknowledged, resolved; escalation of critical unacknowledged after 2 h),
    `water_stress` critical after 48 h, `heavy_rain_forecast` critical with saturated soil.
  - Rule sources (docs/06 §3 table): reading thresholds evaluated in the ingestor after each batch
    (`heat_stress`, `waterlogging`, org custom threshold rules, `water_stress` trigger a); node
    health every 5 min in the worker (`node_offline`, `node_battery_low`, to the technician);
    forecast (`heavy_rain_forecast`) after the 3 h weather refresh; `fungal_risk` daily; balance
    (`water_stress` trigger b, `Dr > RAW`) after E6's daily balance.
  - Outbox (docs/06 §4, ADR-0016): alert and notifications in one transaction; worker delivery with
    `FOR UPDATE SKIP LOCKED LIMIT 50`, backoff 1 min / 5 min / 30 min / 2 h, max 5 attempts then
    `failed`; 410 Gone deletes the subscription and tries the next channel; per-provider circuit
    breaker (5 consecutive failures → open 5 min, criticals go to the alternate channel); quiet
    hours 20:00–05:00 America/Bogota (critical only, the rest at 05:00); grouping of non-critical
    alerts of the same farm within 15 min; channels by severity (info in-app only, warning push,
    critical push + SMS/WhatsApp escalation).
  - Adapters (ADR-0021): Web Push real in both profiles (VAPID); SMS/WhatsApp seminar adapter logs
    and shows in `GET /dev/outbox`; production SMS provider stays future (ADR-0016 scope).
  - API (docs/04 §Alertas y notificaciones, `/dev/outbox` docs/04:182, SSE `alert.opened` /
    `alert.updated` docs/04:193-194 through `NOTIFY plot_events`, ADR-0015).
  - Web: service-worker push handler and push-subscription registration (so "llega un push" is
    real in the browser), reusing E1 primitives (design frozen).
- Out:
  - Model rules `flood_risk` / `drought_risk` evaluation: seeded inactive; wired when E10 lands
    (docs/10:70).
  - `sensor_suspect` (docs/09:47): not in docs/06 §3 factory rules; left for a later epic.
  - Alert list/tray screens and the 05:00 morning irrigation push content: E9 / E6 follow-up
    (coordination note: E6 leaves the informative irrigation push to wire through this outbox once
    both are on main).
  - `alert.outcome` (confirmed/false_alarm) capture: E11 needs it; no endpoint in docs/04 today.
  - Scenario YAMLs, fixtures and `/dev/scenarios/{name}:load`: E16. E7 proves scenario A with an
    integration test that replays scenario-A readings through the ingest path.
  - Production SMS/WhatsApp provider and Prometheus `alert_dispatch_latency_seconds`: production
    profile (ADR-0021).

## Constraints
- ADR-0002 (hexagonal, import-linter layers already list `alerts` and `notifications`), ADR-0003,
  ADR-0012 (procrastinate; jobs idempotent), ADR-0015 (SSE via `NOTIFY plot_events`, payload
  ≤ 8 KB: ids and minimal data), ADR-0016 (outbox), ADR-0021 (seminar), ADR-0022 (`water_stress`
  per plot from `water_balance_daily.stress_moisture_pct`), docs/04 conventions (problem+json,
  404 across orgs, cursor pages), docs/09 (every repository filters by `org_id`; org isolation
  tests).
- Module edges (docs/05): `alerts` may call the public `application` facade of `telemetry`,
  `weather`, `farms` (via telemetry/weather deps), `risk`, `notifications`; never the reverse. The
  ingestor (telemetry) and the weather jobs must not import `alerts`: the reading-rule hook is
  injected by a composition root (ingestor entrypoint), and the forecast rule runs as its own
  alerts periodic job after the refresh.
- E6 boundary (coordination note, docs/06 §3 "Balance hídrico", §5, ADR-0022): E6 owns
  `water_balance_daily`, its migration, the 04:30 balance job and `irrigation_recommendation`.
  E7 never creates them. Both `water_stress` triggers read E6's table: those tasks (T10) run LAST,
  after E6's `water_balance_daily` is on `main` (rebase then). Everything else does not wait.
- Migrations chain from head `b7e2c9a41d38`. E6 also adds one off the same head: whichever
  merges second re-chains its `down_revision` onto main's new head before its PR.
- Reuse: `shared/jobs.py` + `telemetry/adapters/jobs.py` (same-transaction defer via
  `procrastinate_defer_jobs_v1`, `queueing_lock`), `weather/adapters/jobs.py` (periodic fan-out,
  worker `TZ=America/Bogota`), `weather/adapters/open_meteo.py` `CircuitBreaker` (move to `shared`
  if reused, do not copy), `telemetry/adapters/sse_hub.py`, `shared/errors.py` `ProblemError`,
  `identity` `resolve_org_membership`, `farms` `resolve_plot_access`, per-router cursor pages,
  `shared/config.is_seminar_profile()` for adapter selection.
- Ports only for external I/O needing a test double: push sender and SMS/WhatsApp sender.
- New dependency: `pywebpush` (Web Push needs VAPID signing and payload encryption; not a few
  lines). Confirm version and API with `find-docs`; pin in `server/pyproject.toml` + `uv.lock`.
- CodeGraph first (owner 2026-09-26): `codegraph status` in the worktree root (indexed: 270 files);
  `codegraph_explore` or the read-only CLI (explore, query, node, callers, callees, impact,
  affected) before any grep/find/read; fall back only if it fails and say so. Every writer and
  subagent brief carries this rule.
- Ponytail full. Library docs via `find-docs` (ctx7). English code; Spanish only in user-facing
  notification copy (docs/07).

## Route and checks
- TDD: on (owner decision 2026-09-22, `AGENTS.md` Testing). RED with a targeted
  `uv run pytest path::test`, GREEN, REFACTOR. Owner rule for E7: the full suite runs once at the
  end of each task, not after every step; on failure re-run only the failing tests.
- Checks per task (server/, `DATABASE_URL=postgresql+asyncpg://techcamp:techcamp@localhost:5437/techcamp`,
  container `techcamp-e7-db`; never 5432 or 5436): `uv run pytest`, `uv run ruff check`,
  `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`. Web (T9): `npm run lint`,
  `npm run typecheck`, `npm test -- --run`, `npm run build`, `npm run size`.
- Route: delegated direct. Planner/orchestrator: this Claude Code session (Opus 5.5, owner
  2026-09-26). Writers through Herdr, one tab per writer, one session per task group; a new task
  number gets a fresh session. Writer rule (owner 2026-09-26, supersedes the AGY-first rule):
  **OpenCode is the primary writer for every implementation unit; AGY is no longer used**; the
  Claude `odd-worker` subagent only for complex units. Writers never plan. Triggers fired: mapping
  (12+ docs and 5 modules, one Sonnet mapper), writer (every unit touches 2+ non-trivial files).
- Slice flow: OpenCode commits and waits → the parent runs its checks (targeted tests, ruff,
  format, mypy, lint-imports) and compares the diff with the owning docs → quality issues
  (duplication, loose types, dead fallbacks, doc contradictions) are fixed immediately by the same
  OpenCode session → the parent says go → OpenCode runs its own RDD and reports → the parent files
  non-blocking findings as one issue per review round (fixed later) and records the outcome.
- Skills forwarded: `fastapi`, `pydantic`, `find-docs`, `work-unit-commits`, `systematic-debugging`,
  ponytail; `impeccable` for T9 only (E1 design frozen: reuse, no visual changes);
  `domain-modeling` whenever a unit changes docs/03, docs/04 or docs/06.
- Single lane, one worktree (`e7-alerts`, branch `feat/e7-alerts` from `main` @ `a899aa6`), one
  writer at a time.
- Delivery: `stacked-to-main` chained PRs (skill `chained-pr`), about 400 authored lines each,
  merged in order by the owner. Push and open PRs when a slice is ready and green; never merge.
  Forecast ≈ 4,600 authored lines (E4/E5 ran ~1.5× over forecast).
- RDD: on (global). `gentle-ai review assess --cwd <worktree> --agent claude-code --base-ref
  <last reviewed boundary> --committed-only --json` per work-unit commit; first boundary is the
  branch point `a899aa6`. Consent for new-feature candidates: standing grant. Blocking findings:
  bounded correction. Non-blocking: one issue per review round (`review-follow-up`, `epic:e7`,
  `area:*`, `type:*`), fixed later in the epic by the same session, commits `Refs #N`.
- RDD ownership (owner 2026-09-26, clarified the same day): the parent runs the gate and RDD for
  every AGY slice (AGY has none); OpenCode runs its own tests and RDD and reports the outcome. Watch each writer with a poll that wakes on the first `blocked` and on an `idle`/`done`
  held ~45 s, confirmed by reading the pane; the moment a work-unit commit lands, assess it and
  follow `next_transition` verbatim when `review_due`; record the outcome under Review (RDD).
- Parent gate before RDD (owner 2026-09-26; AGY slices only): for every AGY slice the parent runs, in the
  worktree, the slice's targeted tests (`uv run pytest tests/alerts tests/notifications` or the
  touched modules), `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`,
  `uv run lint-imports`, and compares the slice's domain diff against the owning docs (docs/06
  §3–§4, docs/03, ADR-0016, plus the unit's cited sections). Only then RDD. Results recorded per
  slice under Progress.

## Decisions
Readings closest to the docs; each one that changes a doc is written into that doc in the same
work unit (`domain-modeling`).
- D1 Sustained conditions are evaluated statelessly over the stored readings of the window
  (`min_duration_min` back from the batch's latest reading time), not with an in-memory timer.
  docs/06 §3 says `Pending` is not persisted as an alert; computing it from `reading` keeps it
  correct across ingestor restarts and during a backfill with past `ts` (scenario A opens on day
  ≈ 10.7 of the backfill, docs/06 §10). The window counts as sustained when every valid reading
  (quality flag 2 excluded, docs/06 §1) in it meets the condition and the readings span the whole
  window. Update docs/06 §3 with this rule (T2).
- D2 Resolution: condition false beyond the hysteresis band sustained 60 min (docs/06 §3 example:
  resolves with > 15.3 + 3 sustained 1 h). The 60 min is a domain constant (no column in
  docs/03).
- D3 Escalation is not a stored state (`alert.state` stays `open|acknowledged|resolved`,
  glossary): a new `alert.escalated_at` marks it. `POST /alerts/{id}:resolve {note?}` stores the
  note in a new nullable `alert.resolution_note`. Both added to docs/03 in T1.
- D4 Recipients: plot alerts go to org members with role `owner` or `producer`; node alerts and
  escalations go to `farm.technician_id` (docs/06 §3, docs/01 scenario C); with no technician
  assigned they fall back to the org owners. `viewer` never receives notifications. Open question
  Q1.
- D5 Channels (docs/06 §4): `info` creates no `notification` row (in-app only, SSE); `warning` one
  `push` row per recipient; `critical` one `push` row per recipient plus, on escalation, one `sms`
  row to the technician. A severity upgrade to critical (`water_stress` at 48 h,
  `heavy_rain_forecast` with saturated soil) notifies again as critical.
- D6 Quiet hours are applied when the row is written: a non-critical notification created between
  20:00 and 05:00 America/Bogota gets `next_attempt_at` = next 05:00. Grouping: the dispatcher
  sends every due non-critical push of one (user, farm) as a single message; a non-critical row
  created within 15 min of a still-pending one for the same (user, farm) takes that row's
  `next_attempt_at`.
- D7 Delivery trigger: the outbox write defers a procrastinate dispatch job in the same
  transaction (ADR-0012, RNF-05 latency), and a periodic sweep every minute picks up retries and
  anything left over. procrastinate cron has minute resolution, so docs/06 §4 and docs/10 §3
  "cada 5 s" become "at insert + every minute" (doc update in T7a).
- D8 `GET /dev/outbox` lists `notification` rows with channel `sms`/`whatsapp` joined to their
  alert; the seminar SMS adapter only logs and marks them `sent`. No extra table.
- D9 The reading-rule hook: `telemetry` must not import `alerts`, so the ingestor accepts an
  after-flush callback (plot ids + inserted readings) and the ingestor entrypoint composes it with
  the alerts evaluator. The SSE hub learns the `alert.opened` / `alert.updated` event kinds
  without importing `alerts` (payload carries `farm_id` like the others).
- D10 The forecast rule runs as an alerts periodic job a few minutes after each 3 h weather
  refresh (docs/10 §3 k → l) instead of being called by the weather job (no `weather → alerts`
  edge). `fungal_risk` runs daily over the previous day. Node health runs every 5 min.
- D11 Factory rules are read-only for orgs (`PATCH` of an `org_id = null` rule → 404); orgs add
  their own threshold rules with `POST /alert-rules` (role `owner`). `flood_risk` /
  `drought_risk` rows are seeded; no evaluator until E10.
- D12 The 2 h escalation clock runs from `opened_at` (no extra "critical since" column): a
  `water_stress` alert upgraded at 48 h has been unacknowledged far longer than 2 h and escalates
  at the next check. Manual resolve only from `acknowledged` (docs/06 §3 diagram); invalid
  transitions → 409.
- D14 docs/05 lists `alerts → telemetry, weather, risk, notifications` only, but alerts need the
  plot soil, the farm technician and org members, and notifications need push subscriptions and
  phones. T3 adds `alerts → farms`, `alerts → identity` and `notifications → identity` to docs/05
  (no cycles: neither farms nor identity depends back). Cross-module writes share the
  `AsyncSession` as E5 T2 did (`farms` → `weather` repository).
- D13 Factory rule values that docs/06 §3 does not give (hysteresis of `heat_stress` 1 °C,
  `waterlogging` 3, `fungal_risk` 5 %, `node_battery_low` 0.1 V) are seeded as pending
  agronomist validation (docs/06 §3: "Los umbrales se validan con un agrónomo antes del piloto").
- D23 T6b's two rules run on their own alerts periodics (D10), and the hours docs/10 §3 does
  not name are fixed here: the forecast rules at `10 */3 * * *` (ten minutes after the 3 h refresh
  begins) and `fungal_risk` at `45 4 * * *` (after the 04:30 balance, before the 05:00 morning
  push). The forecast cron carries one honest assumption: it reads whatever `weather_daily` holds
  ten minutes after the refresh began, so a slower provider leaves one round reading the previous
  forecast — the job is idempotent and the next round catches up, which is why the parent does not
  prefer a hook inside the refresh (D10's reason, no `weather → alerts` edge, also holds). The
  fan-out is per organization like T6a (D21), with a per-run cache keyed by cell id so plots
  sharing a cell read the forecast rows once, and no cross-org plot read: a weather cell is shared
  by design.
- D22 The 60-minute resolution window belongs to the RULES WITH A SERIES. docs/06 §3's
  "resolución automática ... sostenida durante 60 minutos" exists because a reading rule is
  evaluated over a time series and the clear condition needs to hold, not flicker.
  `heavy_rain_forecast` (every 3 h over a daily aggregate) and `fungal_risk` (daily over the
  previous day) have no evidence finer than their own cadence, so a 60-minute sustained clear
  run is not computable: with one sample per evaluation the run is 0 and the alert would never
  resolve. Those rules resolve on the FIRST false evaluation, hysteresis still applying, and
  docs/06 §3 says so. Same reasoning, same shape: a window in units the evidence does not have
  is not a safe default, it is a rule that can never fire.
- D18 T6a delivers the node-health sweep and `node_offline`, and `node_offline` becomes computable
  by naming the absence of evidence in the domain: a pure `alerts/domain` decision over the time
  since the node's last reading (3 × `interval_s`), not a sample series faking a threshold —
  `decide_alert` cannot do it, because a rule with `operator = None` short-circuits to
  `NO_ACTION` (docs/06 §3's rule table and the seeded row disagree with the domain's own
  contract). `node_battery_low` stays seeded and INACTIVE: there is no `battery_v` column, no
  `battery_v` sensor (the simulator provisions only `soil_moisture`) and `get_node_health` already
  hard-codes the field to `None` with the gap flagged. Inventing a battery protocol no node sends
  is scope this epic does not have; docs/06 §3 gets one honest line saying the rule waits for a
  source, and the issue names the open decision (a `battery_v` sensor through the existing ingest
  path vs a `node.battery_v` column fed by the status payload). Severity `info` means the rule
  would plan no notification anyway (`_drafts`), so nothing is lost by deferring it.
- D19 `fungal_risk` is rewritten to the data the product actually stores: the cell-day
  `rh_mean_pct > 85 %` and a mean temperature of 20–30 °C derived as `(tmin_c + tmax_c) / 2`, with
  no duration. docs/06 §3 asks for "≥ 10 h in the day", which no stored column can answer (one
  scalar per cell-day; Open-Meteo is called with five DAILY variables), and hourly weather is an
  infrastructure unit of its own (table, hourly variables, migration), not part of T6b. The
  agronomic value is kept — a humid mild day is the real disease risk — and only the duration
  nuance is lost, in the same spirit as D13's pending agronomist validation.
- D20 `heavy_rain_forecast` asks for "> 50 mm in 24 h": a day IS 24 h, so the rule reads the
  forecast day's `rain_mm` directly and needs no window method and no new rule column. "Crítica si
  el suelo está saturado" is computed as the plot's latest soil-moisture reading at or above its
  field capacity (the signal E4/E6 already store), and the severity is decided WHEN THE ALERT
  OPENS, not by opening `warning` and upgrading: one write, one notification, the right severity
  from the start. `decide_alert`'s UPGRADE branch stays hardcoded to `water_stress`, so the
  evaluator passes the severity to `open_alert` instead of a second write. The saturation test is
  a PROXY and docs/06 §3 says so: true saturation is soil ABOVE field capacity, and reading "at
  or above" fires at the boundary rather than past it, so it errs toward alerting a plot that is
  merely at capacity. Pending agronomist validation, in the same spirit as D13.
- D21 T6a sweeps nodes per organization: the periodic job reads the org ids and defers one job
  per org, exactly like the existing per-cell (`_defer_cell_job`) and per-plot
  (`_defer_plot_job`) fan-outs, with the same `procrastinate_defer_jobs_v1` inside
  `begin_nested` and per-entity lock strings. No cross-org read is introduced: `list_for_org`
  requires `org_id` on purpose (docs/09 org isolation) and a first cross-org query is not worth
  the machinery it saves. Inside an org the node page is bounded (`limit` + cursor), the
  `telemetry.NodeRepository.list_for_org` shape, not a full pagination design.
- D17 A reading-threshold rule is decided from the plot's sensor series, so the plot evaluator
  selects rules by SOURCE, not by "has a metric and an operator": docs/06 §3's five sources seed
  rules that carry a metric too (`fungal_risk` is `air_rh > 85`, `node_battery_low` is
  `battery_v < 3.4`, `heavy_rain_forecast` is `rain > 50`), and `fungal_risk` would otherwise
  open from the ingestor on a pure RH run, ignoring the 20–30 °C half of its condition and
  stealing T6b's rule. `alert_rule` has no source or target column (docs/03:272-283), so the
  taxonomy lives in the domain as the set of codes that belong to the other four sources
  (`NON_PLOT_RULE_CODES`), exposed as one function that returns the sensor metric a plot rule is
  decided on. Org rules keep working: they are plot rules by construction, with their own code.
  `water_stress` stays in the set (T10 supplies its plot threshold). Follow-up: docs/03 should
  carry a `source` column so the taxonomy is data, not a code list.
- D16 T5's hot evaluation runs in its own transaction, not in the readings' one. The ingestor
  commits each batch inside its repositories (`insert_batch` commits in
  `telemetry/adapters/repositories.py:463`, and the node update commits too), so no transaction
  is open where the alert could be written; docs/06 §1's "misma transacción" line described an
  intent the code never had. What stays atomic is the alert together with its `notification`
  rows (ADR-0016, docs/06 §4, already true in `SqlAlchemyAlertRepository.insert`). The lost
  window is self-healing because the evaluation is stateless over the stored readings (D1): any
  rule that can open needs a sustained series (`min_duration`), so a batch that lands without
  its alert is re-evaluated by the next batch of that plot and opens it then. T5 corrects
  docs/06 §1 to state that guarantee instead of the old one, and keeps the transactional
  refactor out of the epic on purpose: making `ingest_uplinks` own the transaction would touch
  the status path and the flush retry, which is a unit of its own.
- D24 T7a's dispatcher treats a channel with no registered sender as NOT an attempt. `push` has
  no adapter until T7b, and ADR-0016 puts the real SMS/WhatsApp provider in future work, so a
  naive dispatcher would count a missing adapter as a failure and burn all five retries: a row
  would go `failed` before any provider existed, and the escalation SMS the room needs would
  never be simulated either (production registers nothing at all). The row is left `pending` with
  the same due time and counted as `deferred`. Also T7a: the claim's outcome is committed PER ROW,
  not per batch — the message reached the provider, so a crash later in the batch must not leave
  it `pending` and send it again; and `FOR UPDATE OF notification` names the outbox table because
  the join brings `alert` and `alert_rule` in, whose rows must stay writable while the dispatcher
  holds its claim.
- D15 T4 API surface: `GET /alerts` takes the caller's `org_id` and lists only that org
  (`list_alerts(org_id, …)` resolves the membership and then `list_for_orgs([org_id], …)`), never
  every org of the caller; `acknowledge` and `resolve_manually` drop their `farm_id` parameter and
  take the alert's own farm from `get_target_context(...)`, as `upgrade_to_critical` does, so the
  HTTP caller cannot address a foreign farm. `POST /push-subscriptions` upserts on the UNIQUE
  `endpoint` and re-binds the row to the caller, replacing its `keys`; `DELETE` only serves the
  caller's own row (anything else 404). Read `GET /alert-rules` serves any member of the org
  (viewer included); `POST`/`PATCH` need role `owner` of the rule's org.

## E6 coordination (2026-09-26)
- E6 merged to `main` (PRs #104–#111, `main` @ `b627b66`): `water_balance_daily` (`plot_id`, `day`,
  `depletion_mm`, `raw_mm`, `stress_moisture_pct`, `assimilation_k`, `soil_moisture_obs_pct`, …)
  and `irrigation_recommendation`, written by the daily irrigation job at 04:30 America/Bogota
  (the run for day D writes the balance row for D−1; docs/06 §5). Rainfed stress boundary:
  `Dr > RAW` (docs/04:75). E6 Alembic head `d8a2f1c4e9b7`.
- Plan: rebase `feat/e7-alerts` onto `main` right after T3 closes (no writer active), re-chain
  T1's migration `d4e6f8a0b2c1` from `b7e2c9a41d38` onto `d8a2f1c4e9b7`, full checks, then T4.
- Done 2026-09-26: rebased onto `main` @ `b627b66` (one conflict, `server/migrations/env.py`: kept
  both the irrigation and notifications ORM imports); `d4e6f8a0b2c1` re-chained onto
  `d8a2f1c4e9b7` in `6dafb0b`; one Alembic head `d4e6f8a0b2c1`. Old → new SHAs: `2fb93b6`→`c243b71`,
  `18df6c0`→`29fbcef`, `dceaec1`→`01d372a`, `504a682`→`c806f37`, `1d62ba6`→`277b7c7`,
  `75e3630`→`bf59e65`, `2a46a2b`→`c4cd001`, `1355487`→`8890c5e`, `adc7a4a`→`967c9ef`. SHAs elsewhere
  in this doc are pre-rebase.
- The informative irrigation push (docs/06 §5) stays an E6 follow-up to wire through this outbox
  once E7 is on `main`.

## Open questions
- Q1 Plot-alert recipients: docs name the technician for node alerts and escalations and the
  producer for plot alerts, but not whether `owner` also receives plot alerts. D4 includes owners
  (small orgs: the owner is often the producer). Owner to confirm.
- Q2 `water_stress` trigger b (`Dr > RAW` from the balance): E7 evaluates it in an alerts job after
  E6's 04:30 balance job, reading `water_balance_daily` (the row for D−1); E6 did not wire a call
  into its job, so the alerts job stands (settled 2026-09-26 with the E6 coordination note).

## Tasks
- [x] T1 Schema: migration from `b7e2c9a41d38` for `alert_rule` (+ factory rules seeded),
  `alert` (partial unique open index, `escalated_at`, `resolution_note`), `notification`
  (+ `created_at`, `sent_at`, `last_error`), `push_subscription`; ORM rows; docs/03 — route:
  Herdr AGY — forecast ~350 — actual 1,114 (`2fb93b6`; 517 of it tests)
- [x] T2 Alerts domain (pure): sustained window (D1), hysteresis and 60 min resolution (D2), state
  transitions and escalation eligibility (2 h critical, 48 h `water_stress`), threshold per rule
  code; docs/06 §3 — route: Herdr AGY — forecast ~350 — actual 788 (`18df6c0`) + review
  correction 39 by the parent (`dceaec1`)
- [x] T3 Alert lifecycle (AGY draft rejected 2026-09-26: ~2,150 lines vs ~450, SQL in
  `application`, flexible signatures, a `notifications → alerts` cycle; reworked by OpenCode from
  `.git-brief-e7-T3-rework.md`, ~650 forecast; architecture and size rules added to the common
  brief): repository (org-filtered), open/update/resolve use cases writing alert +
  notification rows in one transaction (D4–D6), `NOTIFY plot_events` `alert.opened` /
  `alert.updated`, SSE hub pass-through — route: Herdr OpenCode (rework; AGY draft rejected) —
  forecast ~650 — actual `1d62ba6` (872 prod / 759 tests) + corrections `75e3630`, `2a46a2b`,
  `1355487` (parent, 89 lines incl. the handoff doc)
- [x] Q1 Quality unit for #95 (T1: frozen seed copy in the migration, CHECK/partial-index
  rejection tests, DB defaults, deterministic survival test, drop `seed_factory_rules_sync`) and
  #98 (T2: 48 h upgrade only while the violation run is active, resolved `current_alert` as none,
  `max_gap` = 3 × `interval_s` gap/freshness rule in `sustained_run` written to docs/06 §3, remove
  coercions/alias/`Decimal | float`) — brief `.git-brief-e7-Q1.md` — route: Herdr OpenCode
  `e7-q1` — forecast ~250 — actual 932 (`3fc9b93` 441, `a32a0d0` 491; production 234 / tests 698:
  frozen seed copy + tests); #95 and #98 closed
- [x] T4 Alerts API: `GET /alerts` (cursor page), `:acknowledge`, `:resolve {note?}`,
  `GET/POST/PATCH /alert-rules`, `POST/DELETE /push-subscriptions`; org isolation tests;
  docs/04 shapes — route: Pi subagent (same session, no Herdr tab) — forecast ~450 — actual
  1,540 (`e15b969`; 857 production / 672 tests / 11 docs) + correction 119 (`577a405`)
- [x] T5 Reading rules in the ingestor: after-flush hook (D9), `heat_stress`, `waterlogging`
  (field capacity + 5 from the plot soil), org custom threshold rules; open and resolve on each
  batch — route: Pi subagent (same session, no Herdr tab) — forecast ~450 — actual 4 commits,
  1,059 authored (`7e98815` 721, `4e7201d` 111, `db19ece` 109, `a204e92` 118) + decisions
  D16, D17 — **validation pending**: the lineage is closed at `correction_required` because the
  correction-plan capture is refused (see Review (RDD))
- [ ] T6 Worker rules
  - [x] T6a Node health every 5 min: `node_offline` (no readings for 3 × `interval_s`),
    `node_battery_low` (latest `battery_v` < 3.4 V), to the technician — route: Pi subagent —
    forecast ~350 — actual 898 (`21fb000`; 372 production / 522 tests / 4 docs). `node_battery_low`
    is deliberately NOT delivered: no `battery_v` source exists (D18)
  - [x] T6b `heavy_rain_forecast` after the 3 h refresh (D10), critical with saturated soil;
    `fungal_risk` daily — route: Pi subagent — forecast ~400 — actual 1,660 (`e293b9d` + `e769ebe`
    formatting) + the parent's T6a fix `77827a0` and the timezone fix `016df59`. Three parent
    corrections during the unit: real humidity evidence plus a mild flag instead of the
    `_NOT_MILD_RH` fake sample, the docs/06 §3 rows in the same commit, and the saturation test
    recorded as a proxy (D20)
- [ ] T7 Notifications outbox
  - [x] T7a Dispatcher: sender port, claim `FOR UPDATE SKIP LOCKED LIMIT 50`, backoff and max 5
    attempts, same-transaction defer + per-minute sweep (D7), seminar SMS adapter,
    `GET /dev/outbox` (D8); docs/06 §4, docs/10 §3 — route: Herdr OpenCode —
    forecast ~450 — actual 1,250 (`836e7f1`, 336 prod / 835 tests / 79 docs+config)
  - [ ] T7b Web Push adapter (`pywebpush`, VAPID keys from config), 410 Gone deletes the
    subscription and tries the next channel — route: Herdr OpenCode — forecast ~300
  - [ ] T7c Per-provider circuit breaker (reuse the weather breaker via `shared`), critical
    fallback to the alternate channel, grouping and quiet hours at send (D6) — route: Herdr
    OpenCode (same session as T7a) — forecast ~400
- [ ] T8 Escalation job: critical unacknowledged ≥ 2 h → `escalated_at` + SMS to the technician
  (D4), `alert.updated`; severity upgrade notifications (D5) — route: Herdr OpenCode — forecast ~300
- [ ] T9 Web push client: service-worker `push` / `notificationclick` handlers, subscription
  registration against `POST /push-subscriptions`, one entry point reusing E1 primitives — route:
  Herdr OpenCode + `impeccable` — forecast ~300
- [ ] T10 `water_stress` (after E6's `water_balance_daily` is on `main`; rebase first): trigger a
  over readings vs `stress_moisture_pct` with a representative sensor, trigger b `Dr > RAW`
  without one (ADR-0022, Q2) — route: Herdr OpenCode — forecast ~450
- [ ] T11 Close: scenario-A integration test (readings through ingest → `heat_stress` and
  `water_stress` open → push via the fake sender → escalation SMS in `/dev/outbox`), retries and
  escalation proven; seminar-stack demo; final report — route: Herdr OpenCode — forecast ~250

## Acceptance criteria
- [ ] One non-resolved alert per rule and plot/node, enforced by a partial unique index.
- [ ] Scenario-A readings open `heat_stress` and `water_stress` at the documented times and not
  before; hysteresis prevents flapping.
- [ ] Each warning/critical alert and its notification rows are written in one transaction; a
  push arrives (fake sender in tests, real browser in the demo); the escalation SMS appears in
  `GET /dev/outbox`.
- [ ] Retries follow 1 min / 5 min / 30 min / 2 h and stop at 5 attempts with `failed`; the
  breaker opens after 5 consecutive failures and criticals switch channel.
- [ ] Every alert, rule and subscription endpoint is org-isolated (404 across orgs).
- [ ] All server (and web, for T9) checks green; RDD per work-unit commit.

## Review (RDD)
- Boundary: `a899aa6`.
- T1 (`a899aa6..2fb93b6`, 1,352 lines incl. the plan commit): medium, `slice_budget_reached`;
  standing grant applied by the parent; lineage `review-d45b8465e54dc0d2`, one reliability lens,
  APPROVED and acknowledged (authority burned). 2 WARNING + 3 SUGGESTION, non-blocking → #95
  (frozen seed copy in the migration, CHECK/partial-index rejection tests, DB defaults, survival
  test, unused sync seed helper), fixed later in the epic. Boundary → `2fb93b6`.
- T2 first lineage `review-d81163eb0bd38aa0` (`2fb93b6..18df6c0`): medium, standing grant; one
  reliability lens → CRITICAL `R3-open-without-violation` (deterministic: `sustained_run` returned
  0 for "no run", so a `min_duration = 0` rule opened on empty or healthy samples). Correction plan
  40 lines; parent fix `dceaec1` (TDD: RED `assert <AlertAction.OPEN> == <AlertAction.NO_ACTION>`;
  no run is now `None`). Targeted validation then failed twice with Gentle AI
  `repository_context_unavailable: invalid rctx2 repository context` (retry-safe, nothing
  mutated; worktree had the uncommitted feature doc — unproven cause). Owner chose "Stop here",
  then asked to restart: lineage abandoned (`operator_disposition`, quarantined with its audit
  record), feature doc committed (`504a682`) so the worktree was clean.
- T2 fresh lineage `review-b7aa3a990b41271e` (`2fb93b6..504a682`, incl. the correction): medium,
  standing grant; one reliability lens, APPROVED and acknowledged (authority burned). 3 WARNING +
  1 SUGGESTION → #98 (48 h upgrade while recovering, resolved `current_alert`, gap tolerance in
  `sustained_run`, untested coercions). Boundary → `504a682`.
- T3 (OpenCode `e7-t3-rework`): `1d62ba6` feat (872 prod / 759 tests; parent gate passed after 4
  quality fixes: required identity types, facade-only cross-module imports, dead branch, duplicated
  farm lookup). Lineage `review-e4a6f7fd775b3821`: CRITICAL R3-001 (stale last-write-wins
  transitions, double critical outbox) and R3-002 (event published to a foreign farm) → correction
  `75e3630` (154 lines); targeted validation FAILED on the concurrent half of R3-001 (CAS guarded
  state only) → escalated, `native_stop_required` (terminal). Fix `840d309` → amended by the writer
  to `2a46a2b` (tests only; gate re-verified: no src change): CAS guards state + severity. WARNINGs
  of that lineage, to file: R3-003 `open_alert` accepts an org rule of another org
  (`use_cases.py:52-71`); R3-004 `sse_hub.py:266-270` forwards alert events without required
  fields.
- T3 fresh lineage `review-52ae082b2d0f892b` (`034fe32..2a46a2b`): `correction_required`, budget 200,
  CRITICAL R3-001 `repositories.py:322-323` "DISTINCT with ORDER BY created_at is invalid on
  PostgreSQL" — the writer says false positive (SQLAlchemy `.distinct(user_id)` renders `DISTINCT ON
  (user_id)` with `ORDER BY user_id, created_at DESC`, valid; tests pass) and proposes option (b):
  pin the grouping winner with a dedicated test, no query change; CRITICAL R3-002 real:
  `upgrade_to_critical` upgrades a RESOLVED warning (needs a state guard + test).
  Parent verified R3-001 by compiling the statement with the postgresql dialect:
  `SELECT DISTINCT ON (notification.user_id) … ORDER BY notification.user_id,
  notification.created_at DESC` — valid (the DISTINCT ON key leads the ORDER BY); false positive.
  Correction `1355487` by the parent (owner: "resolve T3 yourself"), TDD: RED `DID NOT RAISE
  InvalidAlertTransitionError`; `Alert.upgrade_to_critical()` refuses a resolved alert, the use case
  calls it; `test_the_pending_group_is_each_users_newest_push_row` pins the grouping winner (passes
  on PostgreSQL, no query change). Gate: tests/alerts + notifications + test_sse_stream 78 passed;
  ruff, format, mypy, lint-imports green. Plan captured (89 lines ≤ 200); the OpenCode-bound
  targeted validator ran in a fresh OpenCode session `e7-t3-validate` (the `e7-t3-rework`
  session was gone) → APPROVED and acknowledged (authority burned, target
  `sha256:bf5bb780…`). The host transport does not surface the validator's follow-ups. Non-blocking
  R3-003, R3-004 → #112. Boundary → `1355487` (rebased `8890c5e`).
- Re-chain `6dafb0b` (`8890c5e..6dafb0b`, 29 lines): medium, `review_due: false`,
  `under_budget` — stays pending in the slice until a later commit reaches the budget.
- Q1 (`8890c5e..a32a0d0`, incl. re-chain `6dafb0b` and parent docs; 979 lines): medium
  (`executable_change` on the migration), `slice_budget_reached`; OpenCode `e7-q1` ran its own RDD,
  standing grant; lineage `review-c9b320d3dc674756`, one reliability lens, APPROVED and acknowledged
  (authority burned), no correction. 2 WARNING non-blocking → #113 (seed `metric`/`hysteresis`/
  `crop_id` not pinned independently; DB-default test bounded by process clock). Boundary →
  `a32a0d0`.
- T6b (`37f6be9..016df59`, 17 files, 1,607 changed lines): medium, one reliability lens, CRITICAL
  `R3-forecast-uses-utc-date` — **real, and fixed**. Both worker jobs took the day from
  `datetime.now(UTC).date()`, but docs/10 §3 fixes every job hour to America/Bogota: from 19:00
  to 23:59 local the UTC date is already the next day, so the forecast day was two calendar days
  ahead and the rule could warn a farmer about the wrong weather (a run delayed across UTC
  midnight also changes the day). `016df59` uses `local_date()`, the same `ZoneInfo` precedent
  `weather/adapters/jobs.py:66` already sets, with a test on an instant that is 20:00 of the
  previous day in Bogota. The writer missed an established pattern the parent had quoted in the
  brief; the same shape now lives in three modules, so one `shared/` calendar helper is the
  follow-up. The lineage is stuck at `correction_required` (tooling blocker below).
  - The parent's T6a fix `77827a0` (owned by the parent, not the writer): a never-reported node
    alerted five minutes after being claimed, while the technician was still installing it
    (docs/06:78). The silence is now measured from `last_seen_at or claimed_at`, and a node with
    neither is not judged; RED was `TypeError: decide_node_health() got an unexpected keyword
    argument 'claimed_at'`. Same commit: the per-org job docstring no longer claims a retry
    decides the same thing (it reads `datetime.now(UTC)` at run time, so a retry decides at a
    later `at`, which can only make an alert fire later), and a full node page logs a warning
    naming the organization instead of truncating silently.
- T6a (`1f268cf..21fb000`, 9 files, 899 changed lines): medium, one reliability lens, CRITICAL
  `R3-periodic-task-name-mismatch` — **refuted by the parent, and the refutation is now a test**.
  The claim was that `@app.periodic` receives no task name and therefore configures the schedule
  with procrastinate's default identity, so the worker would never run the sweep. From the
  installed procrastinate: `periodic_decorator` wraps the `Task` object `@app.task(name=…)` built,
  `register_task` keys the schedule by `task.name`, and `defer_jobs` enqueues
  `task.configure(...)` — the same task. At runtime the registry reads
  `'alerts.sweep_node_health' periodic_id='' cron='*/5 * * * *' -> task.name='alerts.sweep_node_health'`.
  The same shape is weather's 3 h refresh and irrigation's daily job, both live in the seminar
  stack since E5/E6; if the claim held, neither would run. The refutation alone would leave the
  invariant unpinned — every job test calls the coroutine directly, so a schedule naming an
  unregistered task keeps the suite green while the worker never runs it — so the parent added
  `server/tests/shared/test_periodic_schedules.py` (`37f6be9`): every schedule must enqueue a
  registered task on the queue the worker listens to, and the node-health sweep must be
  `*/5 * * * *`. Same shape as T3's `DISTINCT ON` false positive, also refuted from the source.
  The lineage could not advance past the correction-plan slot (tooling blocker below).
  - Parent gate on `21fb000`: static checks green, 9 targeted tests; the writer's single full run
    (748 passed) is the slice's. Two writer deviations, both accepted as improvements: the domain
    decision takes `heard_run` instead of `rule` (the 60-minute clear run is NOT computable from
    `last_seen_at` alone — one timestamp says when the node was last heard, not that it was heard
    continuously — so it reuses `sustained_run` with an always-true predicate and keeps the gap
    tolerance in one place), and no new repository method: the org list is an in-adapter
    `select(distinct NodeRow.org_id)` reading ids only, which is what D21's fan-out is for.
  - D22 (new, for T6b): the 60-minute resolution window belongs to rules WITH A series;
    `heavy_rain_forecast` and `fungal_risk` have no evidence finer than their cadence, so they
    resolve on the first false evaluation.
- T5 (`a8f2ea2..a204e92`, 17 files, 947 changed lines): medium, one reliability lens. Two CRITICAL
  findings, both real, both fixed; the targeted validation could not run (tooling, see below), so
  the slice is NOT approved yet.
  - `R3-MixedPlotTimestamp` (deterministic, introduced): `evaluate_landed_readings` used ONE global
    `at` for the whole batch, so a plot whose newest reading was earlier was judged at another
    plot's timestamp and `sustained_run` dropped its series as stale. The parent wrote that defect
    into the T5 brief ("`at` is the batch's latest reading time"); the fix is per-plot `at`
    (`a204e92`).
  - `R3-RetrySkipsAlertEvaluation` (inferential, behavior-activated): the hook was called only
    with the newly inserted readings, so a re-queued batch (nothing inserts, so `new_events` is
    empty) skipped the evaluation entirely — D16's "the next batch re-evaluates" only holds while
    the node keeps sending NEW readings. The hook now runs with the batch's own readings, which is
    idempotent; the SSE fan-out still publishes only landed rows (QoS-1 dedupe, #36).
  - Also fixed before the review, both found by the parent gate, both with a test:
    `4e7201d` (docs/06 §1: a flag-2 reading must not feed a rule; a new `query_valid_raw` read on
    the telemetry port, flag 1 preserved, the API path unchanged) and `db19ece` (D17: the plot
    evaluator selected rules by "has a metric and an operator", which also selected
    `fungal_risk` — it WOULD have opened from the ingestor on a pure RH run, ignoring the
    20–30 °C half of its condition and stealing T6b's rule).
  - **Tooling blocker (2026-09-27, unresolved, three lineages now):** `gentle_review_capture`
    refuses the correction-plan slot with "collectBinding is unknown, expired, or belongs to a
    different session route" on `review-5104b9ca5c76ab6a` (T5), `review-23ebe1e7dc220953` (T6a)
    and `review-59b7a7c4d9c8c889` (T6b), on both workspace roots and on both serializations of the
    binding, while the SAME route works for the reviewer lens, the refuter and the targeted
    validator (all used successfully in T4 and T5). T4 captured the identical slot only after
    several retries, so it is flaky, not a usage error. Every CRITICAL found so far is fixed and
    gated; what is missing is the provider's validation, not the fix. Tracked in #133, with the
    reproduction and the code path to look at; the owner fixes the tooling on that side.
- T4 (`5dbae1d..e15b969`, 24 files, 1,576 changed lines): medium, one reliability lens. START
  requested the per-slice committed range (`baseRef 5dbae1d`, `committedOnly`), not the
  whole-branch range the inspect offered, per the per-slice decision above. Lineage
  `review-c0833989f9f3593b`, one reliability lens, CRITICAL R3-001 (concurrent rule PATCHes
  reverting each other's fields: read-modify-write wrote every mutable column). Correction plan
  120 diff lines (≤ 200) captured; fix `577a405` (field-level update through a domain
  `AlertRuleChanges`, 119 diff lines) with a barrier-synchronised concurrent-PATCH regression
  test; targeted validation APPROVED and acknowledged (authority burned, target
  `sha256:a677d627…`). Non-blocking R3-002 (push-subscription upsert is read-then-insert, so
  two concurrent registrations of a new endpoint hit the UNIQUE constraint as a 500) plus the
  parent's third copy of `_reject_explicit_null` → #114. Two host-transport notes for the next
  slice: the lens needed a model in `~/.pi/gentle-ai/models.json` (created: all four review
  lenses + `review-refuter`/`review-validator` → `opencode/space-bunny-free`, `high`), and a
  `gentle_review_capture` must omit `workspaceRoot` — the collect-binding route is registered
  under the session cwd, so passing the E7 worktree was refused as "different session route".
  Boundary → `577a405`.
- Stop-hook proposals of a whole-branch review from `b627b66` were declined (per-slice lineages).
- Other lineages in the shared store, not E7's: `review-1655892fb60acdfb` (E5, escalated),
  `review-8d4dc4757b571a56` (active, base tree `c5c49cc`; not ours — leave it).
- Lesson: commit the feature doc before running a slice's RDD, so no review context is issued
  on a dirty worktree.

## Progress / evidence
- 2026-09-26: docs read (AGENTS.md, docs/README, 00, 01, 03, 04, 05, 06 §1/§3/§4/§10, 09, 10,
  ADR-0002/0003/0012/0015/0016/0021/0022, E5 feature doc); code mapped (Sonnet mapper, Alembic
  head verified by the parent: `b7e2c9a41d38`; `alerts`/`notifications` are empty skeletons).
  Feature doc created with 11 tasks (14 units).
- T1 `2fb93b6` (AGY): migration `…f8a0b2c1` from `b7e2c9a41d38`; factory rules seeded from
  `alerts/adapters/seed.py` (deterministic uuid5 ids, `ON CONFLICT DO NOTHING`), re-seeded by the
  test teardown after `TRUNCATE organization … CASCADE`. RED: `ModuleNotFoundError: No module named
  'techcamp.alerts.adapters.orm'`. Checks (techcamp-e7-db): pytest 568 passed; ruff, format,
  mypy, lint-imports green. CodeGraph used, no fallback.
- T2 `18df6c0` (AGY): RED `ImportError: cannot import name 'Alert' from 'techcamp.alerts.domain'`;
  writer checks: pytest 577 passed; ruff, format, mypy, lint-imports green. Parent gate: tests/alerts
  + tests/notifications 17 passed; ruff, format, mypy, lint-imports green; diff matches docs/06 §3
  (sustained run, hysteresis, 60 min resolution, 48 h upgrade, manual resolve only from
  acknowledged, 2 h escalation) and D1/D2/D12 bullets added to docs/06 §3.
- T2 correction `dceaec1` (parent): tests/alerts 15 passed; ruff, format, mypy, lint-imports green.
- 2026-09-26: the OpenCode T3 writer ran `git reset --hard` to drop the AGY draft and discarded the
  parent's uncommitted feature-doc edits (restored). Rule added: writers never run destructive git
  commands; the parent commits the feature doc before each writer runs.
- T3 closed 2026-09-26: lineage `review-52ae082b2d0f892b` acknowledged; issue #112 filed.
- Rebase 2026-09-26 (parent): full checks on `6dafb0b` — pytest 687 passed; ruff, format, mypy,
  lint-imports green; `alembic heads` = `d4e6f8a0b2c1`.
- Q1 2026-09-26 (OpenCode `e7-q1`): first RED `TypeError: decide_alert() got an unexpected keyword
  argument 'max_gap'`; writer checks pytest 702 passed, ruff, format, mypy, lint-imports green.
  Parent gate on `a32a0d0`: tests/alerts + notifications + test_sse_stream 93 passed; ruff, format,
  mypy, lint-imports green; diff matches docs/06 §3 (new gap/freshness bullet) and docs/03. Gap rule
  as implemented: a gap > `max_gap` restarts the run (zero-length run, not `None`); a stale latest
  sample is `None`. Note: the `a32a0d0` body says the adapter converts `Numeric` → `float` for rules;
  today only the test helper does — the rule-loading adapter (T4/T5) must do it.
- T4 2026-09-27 (writer: Pi subagent, no Herdr tab): D15 added and committed first (`5dbae1d`).
  Writer evidence: first RED `TypeError: acknowledge() missing 1 required keyword-only argument:
  'farm_id'` (`tests/alerts/test_lifecycle.py:396`); its own checks: pytest 726 passed, ruff,
  format, mypy, lint-imports green. Parent gate on `e15b969` (run by the parent, DB
  `techcamp-e7-db` on 5437): 726 passed, ruff, format, mypy, lint-imports green; diff matches
  docs/04 §Alertas (new shapes and 403/404/409/422 written there in the unit), docs/03 and
  D11/D15; no SQL in the routers, no SQLAlchemy or adapters in `application`, `notifications`
  never imports `alerts`, every read filtered by `org_id`. No quality issue to fix now.
  Correction `577a405`: writer's checks 73 passed in `tests/alerts`; parent gate on `577a405`:
  full suite 727 passed, ruff, format, mypy, lint-imports green. The writer changed the planned
  sequential test for a barrier-synchronised concurrent pair, because a sequential PATCH cannot
  observe the defect (each request would read a fresh row) — accepted, it is the stronger test.
- Next step: T7a (the outbox dispatcher: sender port, claim with `FOR UPDATE SKIP LOCKED`, backoff
  1 min / 5 min / 30 min / 2 h and 5 attempts, the per-minute sweep, the seminar SMS adapter and
  `GET /dev/outbox`, D7/D8), then T7b, T7c, T8, T9, T10, T11. T6a and T6b are code-complete and
  gated; their lineages stay open at `correction_required` because of the tooling blocker, so the
  delivery boundary is `016df59` and the three pending validations should be re-run when it
  clears.
  Two invariants learned from T5's CRITICALs travel with every brief: a decision that reads a
  window is taken at the newest evidence of ITS OWN target, never a global time; and every
  behaviour test carries the negative assertion too, because in an alerting system the dangerous
  failure is silence, not an exception — which is why the whole suite was green through both.
- T7a 2026-09-27 (writer: OpenCode, worktree `e7-t7-outbox` on `feat/e7-t7-outbox` from
  `8cbae7f`, own DB `techcamp-e7-db-t7` on 5439, brief `.git-brief-e7-T7.md`): D7 and D8 as
  written. CodeGraph initialized in the worktree and used for the map (outbox write, notification
  domain, weather breaker, `shared/jobs.py`, the alerts fan-out). `ctx7` was NOT available in this
  runtime (no ctx7 tool exposed), so procrastinate / `SKIP LOCKED` / `pywebpush` were verified
  against the installed package and by compiling the statements with the postgresql dialect
  instead of the docs; that limitation is disclosed here rather than papered over.
  - RED (recorded, not reconstructed): `ImportError: cannot import name 'senders' from
    'techcamp.notifications.adapters'` and
    `AttributeError: SqlAlchemyOutboxRepository.claim_due() missing 1 required keyword-only
    argument: 'limit'` while the modules did not exist yet.
  - Delivered: `NotificationSender` + `OutboxRepository` ports, `dispatch_due_notifications`
    (sent / retried / failed / deferred, backoff, 5 attempts), `SqlAlchemyOutboxRepository` (the
    claim, per-row commits), `SeminarSmsSender` + `build_senders`, `notifications/adapters/jobs.py`
    (insert-time defer in a savepoint + `* * * * *` sweep on the `notifications` queue, worker
    listens to it), `GET /dev/outbox` in the seminar profile, D7's "at insert + every minute" in
    docs/06 §4 and docs/10 §3, the tray's shape in docs/04.
  - `notifications/adapters/outbox.py` is a new module rather than a class in `repositories.py`
    because the dispatch job needs the repository and the job must not import the module that
    enqueues it; the dependency runs one way (`repositories.py` → `jobs.py` → `outbox.py`), the
    same direction `telemetry` and `farms` use to enqueue a neighbour's job.
  - **Defect found and fixed in this unit:** `migrations/env.py` calls
    `logging.config.fileConfig(alembic.ini)`, and `fileConfig` defaults
    `disable_existing_loggers=True`, which sets `disabled` on every logger not named in
    `[loggers] keys` (only `root`, `sqlalchemy`, `alembic`). The first migration therefore
    silenced every `techcamp.*` logger for the rest of the process — which is why no test in this
    repo had ever been able to assert anything the application logs. Fixed by passing
    `disable_existing_loggers=False` (it is a function argument in Python 3.12, NOT a key in
    `alembic.ini` — the first attempt put it in the ini and changed nothing), with the reason
    written in both files. Found because ADR-0021's "writes to the log" had to be testable.
  - Checks (own DB, 5439): `uv run pytest tests/notifications tests/alerts` → 144 passed, 1 failed;
    the failure is `tests/alerts/test_jobs.py::test_the_forecast_job_reads_the_forecast_day_and_
    the_daily_job_the_cell_day`, and it fails identically on the stashed base (`8cbae7f`), so it
    is a pre-existing environmental failure, not T7a's. `uv run ruff check` → All checks passed!
    `uv run ruff format --check` → 237 files already formatted. `uv run mypy` → Success: no issues
    found in 160 source files. `uv run lint-imports` → 1 kept, 0 broken.
  - Size: ~1,210 changed lines against a ~450 forecast — reported, not trimmed. 336 production,
    835 tests, ~40 docs/config. The overage is behaviour tests against real Postgres (this repo's
    stated rule, conftest: "no SQLite double"), because the claim's `SKIP LOCKED` promise and the
    same-transaction defer are only provable against the database.
