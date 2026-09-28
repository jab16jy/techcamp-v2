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
- [ ] T4 Alerts API: `GET /alerts` (cursor page), `:acknowledge`, `:resolve {note?}`,
  `GET/POST/PATCH /alert-rules`, `POST/DELETE /push-subscriptions`; org isolation tests;
  docs/04 shapes — route: Herdr OpenCode — forecast ~450
- [ ] T5 Reading rules in the ingestor: after-flush hook (D9), `heat_stress`, `waterlogging`
  (field capacity + 5 from the plot soil), org custom threshold rules; open and resolve on each
  batch — route: Herdr OpenCode — forecast ~450
- [ ] T6 Worker rules
  - [ ] T6a Node health every 5 min: `node_offline` (no readings for 3 × `interval_s`),
    `node_battery_low` (latest `battery_v` < 3.4 V), to the technician — route: Herdr OpenCode —
    forecast ~350
  - [ ] T6b `heavy_rain_forecast` after the 3 h refresh (D10), critical with saturated soil;
    `fungal_risk` daily — route: Herdr OpenCode (same session as T6a) — forecast ~400
- [ ] T7 Notifications outbox
  - [ ] T7a Dispatcher: sender port, claim `FOR UPDATE SKIP LOCKED LIMIT 50`, backoff and max 5
    attempts, same-transaction defer + per-minute sweep (D7), seminar SMS adapter,
    `GET /dev/outbox` (D8); docs/06 §4, docs/10 §3 — route: Herdr OpenCode —
    forecast ~450
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
- Next step (handoff 2026-09-26): ~~close T3's lineage `review-52ae082b2d0f892b`, file T3 issue
  (R3-003, R3-004 + new WARNINGs)~~, ~~rebase onto `main` @ `b627b66` (re-chain `d4e6f8a0b2c1` onto
  `d8a2f1c4e9b7`)~~, ~~quality unit for #95/#98~~, then T4 (brief `.git-brief-e7-T4.md` written;
  add D15 to Decisions before launching it: `list_alerts` takes `org_id`; `acknowledge`/
  `resolve_manually` drop `farm_id` and take the alert's own farm; push endpoint upsert re-binds
  to the caller). Owner paused after Q1 (2026-09-26). Handoff prompt: `.git-brief-e7-handoff.md`.
