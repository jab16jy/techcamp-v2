# TechCamp v2 — E5 Weather

## Objective
Deliver epic E5 from `docs/10-dag.md:68`: ET0, rain and forecast per weather cell, with `stale`
degradation. Node scope (docs/10-dag.md:16): cells + Open-Meteo.

## Why
E5 depends only on E3 and unblocks E6 (irrigation), E7 (alerts), E10 (risk model) and E16
(scenarios). E6 (critical path) needs daily ET0 and rain per plot.

## Scope
- In (`weather` module, server only):
  - `weather_cell` (0.1° grid, shared across orgs, no `org_id`) and `weather_daily`
    (PK `cell_id, day, is_forecast`; `et0_mm`, `rain_mm`, `tmin_c`, `tmax_c`, `rh_mean_pct`,
    `fetched_at`) (docs/03-modelo-datos.md:176-191); FK `plot.weather_cell_id → weather_cell`
    (docs/03:100; column exists since E3 without FK).
  - Cell assignment for plots: round the plot location to the 0.1° cell, get or create it
    (docs/06-diseno-detallado.md §6, docs/00-glosario.md).
  - Open-Meteo client: daily `et0_fao_evapotranspiration`, precipitation, temperature min/max,
    mean relative humidity (ADR-0009); `httpx` 10 s timeout, 3 retries with backoff and jitter,
    circuit breaker (docs/06 §6).
  - Jobs on procrastinate (ADR-0012): forecast refresh of active cells every 3 h; daily
    consolidation of the previous day as observed (`is_forecast = false`) (docs/06 §6); per cell,
    parallel in the worker (docs/09:29). `POST /dev/jobs/weather:run` (docs/04:177).
  - Degradation: on Open-Meteo failure keep the last stored data, served as `stale` with its
    `fetched_at` (docs/06 §6, docs/09:15, 51).
  - `GET /plots/{plot_id}/weather?days=` → `WeatherDay[]`, forecast plus latest observed
    (docs/04:94), plot access resolved like every other plot endpoint (docs/09 org isolation).
- Out:
  - Web weather widget and `weather_next_3d` in `GET /plots/{id}/status`: E9 home screen
    (docs/07, docs/10 E5 → E9).
  - `sim record-weather`, scenario weather fixtures and `/dev/scenarios/{name}:load`: E16
    (docs/06 §10). E5 leaves the `weather_daily` upsert usable by that loader.
  - Low-confidence flag on irrigation with weather older than 24 h: E6 (docs/06 §6).
  - Risk features from weather: E10 (docs/06 §8).

## Constraints
- ADR-0002 (hexagonal, import-linter), ADR-0003 (one Postgres), ADR-0009 (ET0 from Open-Meteo, no
  own Penman-Monteith), ADR-0012 (jobs in Postgres), ADR-0021 (seminar: free Open-Meteo;
  production: commercial plan), docs/04 conventions (problem+json, 404 across orgs), docs/09.
- Reuse: `farms/adapters/soilgrids.py` (httpx adapter + `MockTransport` test double, seminar
  factory, profile-gated DI), `telemetry/adapters/jobs.py` + `shared/jobs.py` (procrastinate app,
  task pattern), farms plot access resolution, real-Postgres fixtures.
- Port only for Open-Meteo (external I/O needing a test double). No new dependencies: `httpx` and
  `procrastinate` are installed; retry and circuit breaker are a few lines in the adapter.
- Ponytail full. CodeGraph first. Library docs via `find-docs` (ctx7). English code.

## Route and checks
- TDD: on (owner decision 2026-09-22, `AGENTS.md`). RED → GREEN → REFACTOR. Runner:
  `uv run pytest` (server/).
- Other checks: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`,
  `uv run lint-imports`.
- Route: delegated direct. Writers through Herdr: AGY for light and moderately complex units (fast;
  no RDD access, so the parent runs RDD per commit; owner 2026-09-26), OpenCode for heavy units
  (runs its own RDD). Every AGY unit records its evidence here: commit, RED line, checks. Claude subagents use `odd-worker` (owner 2026-09-26,
  replaces `sonnet-high`). One Herdr session per task group (T1a–T1b share); a new task number
  gets a fresh session. Triggers fired: mapping (10+ docs, one Sonnet explorer), writer (each unit
  touches 2+ non-trivial files).
- Skills forwarded: `fastapi`, `pydantic`, `find-docs`, `work-unit-commits`, `chained-pr`,
  `systematic-debugging`, ponytail.
- Parallel lanes (no shared files): lane A `feat/e5-weather` (T1 → T2 → T4) and lane B
  `feat/e5-weather-client` (T3), both from `main` @ `9a06794`, own worktree and own test DB each.
  T5 (jobs) starts after lanes A (T1) and B merge.
- Delivery: `stacked-to-main`, about 400 authored lines per PR, merged in order. Forecast ≈ 3,000
  authored lines (E4 ran ~1.5× over its forecast).
- RDD: on (global). One `gentle-ai review assess --committed-only` per work-unit commit; first
  boundary is the branch point `9a06794`.

## Decisions
- ET0 is Open-Meteo `et0_fao_evapotranspiration` (ADR-0009); no in-house computation.
- `days` accepts 1–16, default 7: RF-11 asks for 7–16 days (docs/01:34); docs/04:94 shows 7 as the
  example. The response carries the last `days` observed days and the next `days` forecast days,
  ordered by day. Historical accumulations (RF-11) are sums over those observed rows; a dedicated
  accumulation endpoint waits until a consumer (E6/E9) needs one.
- `WeatherDay` = `day`, `is_forecast`, `et0_mm`, `rain_mm`, `tmin_c`, `tmax_c`, `rh_mean_pct`,
  `fetched_at`, `stale`. `stale` is true when the cell's latest successful fetch is older than
  6 h (two missed 3 h refreshes). Recorded in docs/04 with T4.
- Cold start: a plot whose cell has never been fetched returns `[]`; creating a new cell defers a
  one-off fetch for it so data appears without waiting for the next 3 h run.
- Active cells = cells referenced by at least one plot.
- `weather_cell` gets `UNIQUE(lat, lon)` so `get_or_create_cell` is idempotent under concurrency; lat/lon go through `Decimal(str(v))` so a float never splits one cell into two (T1a; docs/03 updated in T1b).
- Weather measures are nullable (Open-Meteo returns null for missing days); only `fetched_at` is NOT NULL. `stale` uses `max(fetched_at)` per cell. `WeatherDay` lives in domain from T1a; `stale` is computed, not stored (T1a).
- Jobs: worker runs with `TZ: America/Bogota` (procrastinate evaluates cron in local time); the Open-Meteo adapter is one shared instance in the worker so the circuit breaker state is shared; refresh and consolidation have separate lock namespaces (`refresh:` / `consolidate:`); consolidation dedups per cell and day (T5).
- A plot's cell is the 0.1° cell of its polygon centroid (the SoilGrids point); every PATCH recomputes it (idempotent upsert). A rejected plot may leave an unreferenced cell, which is harmless: it is never fetched (T2).
- `PlotRow.weather_cell_id` stays a plain Integer in the farms ORM (no farms→weather metadata edge); the FK exists in the DB (T1a).
- Seminar calls the free Open-Meteo API; production uses the commercial endpoint with an API key
  (ADR-0021). Tests use `httpx.MockTransport` only.

## Tasks
- [x] T1 Weather schema and domain (lane A)
  - [x] T1a Migration from head `a3f1c7d92b40`: `weather_cell`, `weather_daily`, FK
    `plot.weather_cell_id`; ORM rows; repository with `weather_daily` upsert — route: Herdr OpenCode — forecast ~350 — actual 541 (`68d44ea`)
  - [x] T1b Pure domain: 0.1° cell rounding, `WeatherDay`, `stale` rule (6 h) — route: Herdr OpenCode
    (same session as T1a) — forecast ~150 — actual 142 (`0a4db5d`)
- [x] T2 Plot cell assignment (lane A): assign or reassign the cell on plot create/update of
  location; backfill existing plots (cold-start fetch moved to T5) — route:
  Herdr OpenCode — forecast ~350 — actual 398 (`68704fd`) + review correction 80 (`213605c`)
- [x] T3 Open-Meteo client (lane B)
  - [x] T3a `WeatherForecastPort` + `OpenMeteoAdapter`: daily variables, `past_days`, parsing,
    `MockTransport` tests, seminar/production DI — route: Herdr AGY — forecast ~350 — actual 530 (`22e7dc0`, lane B)
  - [x] T3b Timeout 10 s, 3 retries with backoff and jitter, circuit breaker — route: Herdr AGY
    (same session as T3a) — forecast ~250 — actual 404 (`b0e95e7`) + review correction 30 (`05c4f47`)
- [x] T4 Weather API (lane A): `GET /plots/{plot_id}/weather?days=`, org isolation test, docs/04
  `WeatherDay` shape — route: Herdr AGY (parallel with T5) — forecast ~400 — actual 552 (`f07d66f`)
- [x] T5 Weather jobs (branch `feat/e5-weather-jobs` = lane A @ `afbd9ab` + lane B merged; parallel with T4)
  - [x] T5a 3 h forecast refresh per active cell, parallel per cell, keeps last data on failure;
    cold-start fetch for new cells — route: Herdr OpenCode — forecast ~400 — actual 643 (`705af4d`)
  - [x] T5b Daily consolidation of the previous day as observed; `POST /dev/jobs/weather:run` —
    route: Herdr OpenCode (same session as T5a) — forecast ~300 — actual 697 (`8bf2aec`) + writer correction 28 (`6eecb2f`) + parent fix 50 (`7470287`)
- [ ] T6 Close: end-to-end check in the seminar stack (plot → cell → job → `GET /weather`),
  feature doc final state — route: Herdr AGY (fastest; owner 2026-09-26), on `feat/e5-weather-jobs` after lane A is merged in; parent reviews — forecast ~50

## Acceptance criteria
- A plot gets a 0.1° cell; plots within the same cell share it; plots of different orgs share
  cells but never see each other's plots.
- The refresh job stores 16 forecast days per active cell; consolidation stores yesterday as
  observed.
- With Open-Meteo down, `GET /plots/{id}/weather` returns the last data with `stale: true` and its
  `fetched_at`; the circuit breaker opens after repeated failures.
- All server checks green; RDD per work-unit commit.

## Review (RDD)
- Boundary: `9a06794` (both lanes).
- Lane A T1a (`9a06794..68d44ea`): medium, `slice_budget_reached`; standing grant applied by the OpenCode writer; one reliability lens, APPROVED with zero findings, acknowledged. Lane A boundary → `68d44ea`.
- Lane B T3a (`9a06794..22e7dc0`, 530 lines): medium, `slice_budget_reached`; standing grant applied by the parent; lineage `review-98b481e046967cd6`, one reliability lens, APPROVED and acknowledged. 2 WARNING + 3 SUGGESTION, non-blocking → issue #77, deferred. Lane B boundary → `22e7dc0`.

- Lane A T1b + T2 (`68d44ea..68704fd`): T1b alone was `under_budget` (131 lines) and was reviewed with T2. Medium, `slice_budget_reached`; standing grant applied by the OpenCode writer; lineage `review-3128da3705bbd142`, one reliability lens → CRITICAL `R3-ATOMIC-PARTIAL-PLOT` (plot committed before its cell was assigned) and `R3-RACE-STALE-CELL` (cell computed from an older boundary under concurrent updates) → bounded correction `213605c` (80 lines: boundary and cell written in one statement, cell derived from the same WKT) → APPROVED and acknowledged. No WARNING/SUGGESTION. Lane A boundary → `213605c`.
- Lane B T3b (`22e7dc0..b0e95e7`): medium, slice_budget_reached; standing grant applied; lineage review-d4e015438f4453ed, one reliability lens → CRITICAL `R3-double-count-failure` (each failed operation counted twice by the circuit breaker, opens at ceil(N/2)) → correction plan 40 lines → bounded correction `05c4f47` → targeted validation APPROVED and acknowledged. 3 WARNING + 1 SUGGESTION → #78, deferred. Lane B boundary → `05c4f47`. T5 note: jobs must share one adapter instance so the breaker state is shared.
- Lane A T4 (`213605c..f07d66f`, 552 lines): medium, `slice_budget_reached`; standing grant applied by the parent; lineage `review-ccfbc447f0100ee9`, one reliability lens, APPROVED and acknowledged. 1 WARNING + 1 SUGGESTION, non-blocking → issue #79, deferred. Lane A boundary → `f07d66f`.
- Jobs T5a (`e0612fb..705af4d`): medium, slice_budget_reached; standing grant applied by the writer; lineage review-785c1d8c1afd036e, one reliability lens, APPROVED and acknowledged. 1 WARNING → #80, deferred. Jobs boundary → `705af4d`.
- Jobs T5b (`705af4d..6eecb2f`): lineage `review-1655892fb60acdfb` found CRITICAL: the consolidation queueing lock `consolidate:cell:{id}` carried no day, so a second day asked for one cell while the first waited was silently dropped. The writer's bounded correction `6eecb2f` fixed a different issue (reject today in the dev route); the targeted validation escalated and the lineage stopped with `native_stop_required` (terminal). The parent fixed it in `7470287` (TDD: RED `assert ['2026-09-24'] == ['2026-09-24', '2026-09-25']`; queueing lock `consolidate:cell:{id}:{day}`, `lock` stays per cell), owner request 2026-09-26.
- Jobs T5b fresh review (`705af4d..7470287`): medium, `slice_budget_reached`; standing grant applied by the parent; lineage `review-83dc33d263530aae`, one reliability lens, APPROVED and acknowledged. 2 WARNING + 2 SUGGESTION, non-blocking → issue #81, deferred. Jobs boundary → `7470287`.

## Progress / evidence
- T1a `68d44ea` (OpenCode): RDD approved, zero findings. Follow-up noticed: `tests/telemetry/test_jobs.py::test_two_queued_jobs_for_one_sensor_never_run_at_the_same_time` is order-flaky (~1 in 3 full runs).
- T3a: `22e7dc0` feat(weather): add Open-Meteo forecast adapter — 530 lines (AGY). RED: `ModuleNotFoundError: No module named 'techcamp.weather.adapters.open_meteo'`. Checks (techcamp_e5b): pytest 494 passed; ruff check, format --check, mypy, lint-imports green.
- T1b `0a4db5d` (OpenCode): 9 pure domain tests; pytest 493 passed; all checks green; docs/03 records `UNIQUE(lat, lon)`, nullable measures, plain table.
- T2 `68704fd` + `213605c` (OpenCode): cell = centroid of the plot polygon rounded to 0.1° (docs/06 §6 bullet added); farms repository calls the weather repository and `cell_for` (no port); backfill migration `b7e2c9a41d38` (downgrade intentionally empty: derived data); shared `tests/conftest.py` teardown now truncates `weather_cell`/`weather_daily`. pytest 500 passed; all checks green; parent spot check `lint-imports` kept.
- T3b `b0e95e7` (AGY, ~404 lines). RED: `ImportError: cannot import name 'CircuitState'`. Checks (techcamp_e5b): pytest 502 passed; ruff, format, mypy, lint-imports green. Correction `05c4f47` (30 lines, AGY): RED threshold test then GREEN; pytest 503 passed; all checks green.
- T4 `f07d66f` (AGY): RED `ModuleNotFoundError: No module named 'techcamp.weather.adapters.api'`; pytest 506 passed; ruff, format, mypy, lint-imports green; docs/04 records the `WeatherDay` shape, `days` 1..16, America/Bogota today and the `stale` rule. Follow-up noticed: intermittent Timescale teardown race (`DROP MATERIALIZED VIEW reading_hourly`: tuple concurrently updated).
- T5a `705af4d` (OpenCode, 643 lines): RED `ImportError: cannot import name 'jobs' from 'techcamp.weather.adapters'`, second RED duplicate `procrastinate_jobs_queueing_lock_idx_v1`; pytest 535 passed; all checks green. Choices: worker `TZ: America/Bogota` (procrastinate cron uses local time); `queue=` repeated on `@app.periodic`; cold-start defer in farms `_cell_id_for` on a cold cell (no weather_daily rows), same transaction as the plot (telemetry pattern), the 3 h fan-out is the backstop; per-cell `lock` = `queueing_lock` = `refresh:cell:<id>`; `refresh_cell` has no retry (adapter retries), fan-out max_attempts=2. Follow-up: ADR-0012 same-transaction enqueue vs seminar without the procrastinate schema is undocumented in docs/09.
- T5b `8bf2aec` + `6eecb2f` (OpenCode) + `7470287` (parent): daily consolidation at 03:00 America/Bogota per active cell (`past_days` counted back from the target day; a day the provider does not report is not stored), `POST /api/v1/dev/jobs/weather:run {day?}` seminar only (today rejected: `past_days=0, forecast_days=0` returns no day). Parent checks on `7470287` (techcamp_e5c): pytest 549 passed; ruff, format, mypy, lint-imports green.
- 2026-09-26: E5 mapped (docs/03, 04, 05, 06 §6/§8/§10, 09, ADR-0009/0021). Feature doc created.
