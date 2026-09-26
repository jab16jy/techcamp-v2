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
- Route: delegated direct. Writers through Herdr: OpenCode for technical units (runs its own RDD),
  AGY for light units (parent runs RDD). Claude subagents use `odd-worker` (owner 2026-09-26,
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
- Seminar calls the free Open-Meteo API; production uses the commercial endpoint with an API key
  (ADR-0021). Tests use `httpx.MockTransport` only.

## Tasks
- [ ] T1 Weather schema and domain (lane A)
  - [ ] T1a Migration from head `a3f1c7d92b40`: `weather_cell`, `weather_daily`, FK
    `plot.weather_cell_id`; ORM rows; repository with `weather_daily` upsert — route: Herdr
    OpenCode — forecast ~350
  - [ ] T1b Pure domain: 0.1° cell rounding, `WeatherDay`, `stale` rule (6 h) — route: Herdr
    OpenCode (same session as T1a) — forecast ~150
- [ ] T2 Plot cell assignment (lane A): assign or reassign the cell on plot create/update of
  location; backfill existing plots; defer the cold-start fetch hook (no-op until T5) — route:
  Herdr OpenCode — forecast ~350
- [ ] T3 Open-Meteo client (lane B)
  - [ ] T3a `WeatherForecastPort` + `OpenMeteoAdapter`: daily variables, `past_days`, parsing,
    `MockTransport` tests, seminar/production DI — route: Herdr OpenCode — forecast ~350
  - [ ] T3b Timeout 10 s, 3 retries with backoff and jitter, circuit breaker — route: Herdr
    OpenCode (same session as T3a) — forecast ~250
- [ ] T4 Weather API (lane A): `GET /plots/{plot_id}/weather?days=`, org isolation test, docs/04
  `WeatherDay` shape — route: Herdr OpenCode — forecast ~400
- [ ] T5 Weather jobs (after lanes merge)
  - [ ] T5a 3 h forecast refresh per active cell, parallel per cell, keeps last data on failure;
    cold-start fetch for new cells — route: Herdr OpenCode — forecast ~400
  - [ ] T5b Daily consolidation of the previous day as observed; `POST /dev/jobs/weather:run` —
    route: Herdr OpenCode (same session as T5a) — forecast ~300
- [ ] T6 Close: end-to-end check in the seminar stack (plot → cell → job → `GET /weather`),
  feature doc final state — route: parent — forecast ~50

## Acceptance criteria
- A plot gets a 0.1° cell; plots within the same cell share it; plots of different orgs share
  cells but never see each other's plots.
- The refresh job stores 16 forecast days per active cell; consolidation stores yesterday as
  observed.
- With Open-Meteo down, `GET /plots/{id}/weather` returns the last data with `stale: true` and its
  `fetched_at`; the circuit breaker opens after repeated failures.
- All server checks green; RDD per work-unit commit.

## Review (RDD)
- Boundary: `9a06794`.

## Progress / evidence
- 2026-09-26: E5 mapped (docs/03, 04, 05, 06 §6/§8/§10, 09, ADR-0009/0021). Feature doc created.
