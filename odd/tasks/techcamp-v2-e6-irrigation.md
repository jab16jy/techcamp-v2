# TechCamp v2 — E6 Irrigation (FAO-56)

## Objective
Deliver epic E6 from `docs/10-dag.md:69`: a daily recommendation with `rationale` per plot — a
depth for plots with an irrigation system, a rainfed recommendation for the others — tested
against the FAO-56 numeric examples. Depends on E4 (telemetry) and E5 (weather), both on main.

## Why
E6 is on the critical path (E4 → E6 → E9 → E15) and feeds E7 (the `water_stress` rule reads
`water_balance_daily`), E11 (metrics) and E12 (assistant). It is the product's core decision
(RF-09, docs/01:32).

## Scope
- In (`irrigation` module, server only):
  - Pure FAO-56 single-Kc math (docs/06 §5 table, ADR-0009): Kc per stage with linear
    interpolation during `development`, ETc, TAW, adjusted `p` (clamped 0.1–0.8), RAW, θ_stress,
    Pe (`0.8 × P` if P > 5 mm), modelled Dr (clamped 0–TAW), observed Dr, weighted assimilation
    `Dr = Dr_model + K × (Dr_obs − Dr_model)` with `K` 0 or 0.5 (docs/06 §5, ADR-0022).
  - Recommendation decision (docs/06 §5 flowchart, ADR-0023): `no_kc`, `irrigate` (depth =
    Dr / efficiency, minutes = depth × area_m² / flow_lph × 60), `postpone` (48 h forecast rain ≥
    Dr), `not_needed`, `rainfed` with the ordered advice table (`delay_sowing`, `rain_expected`,
    `conserve_moisture`, `prioritize_harvest`, `no_action`). `rationale` carries the numbers used
    (ET0, Kc and `kc_source`, p, RAW, Dr model and assimilated, K, forecast; "sin sensor" flag).
  - Tables `water_balance_daily` and `irrigation_recommendation` (docs/03:193-215) with migration,
    ORM and repositories filtered by `org_id` (docs/09 org isolation).
  - Daily job at 04:30 America/Bogota (docs/06 §5), fan-out per plot, on procrastinate (ADR-0012),
    following `weather/adapters/jobs.py`; `POST /dev/jobs/irrigation:run {day?}` (docs/04:181).
  - Sensor selection for `K` (docs/06 §5): valid reading in 24 h, `field` calibration,
    representative depth (near Zr/2, or two depths averaged in the root zone).
  - `GET /plots/{plot_id}/irrigation/recommendation?day=` and
    `GET /plots/{plot_id}/water-balance?from=&to=` (docs/04:105-109).
- Out:
  - `water_stress` alert opening (both triggers) and the informative irrigation push: E7 owns the
    rules and the outbox (docs/06 §3 "Balance hídrico" row, §4). E7 reads `water_balance_daily`;
    coordination note sent to the E7 session. Push wiring is a follow-up once both are on main.
  - Logbook irrigation (`I` in the balance): E8 (`logbook_entry` does not exist). Until then
    `irrigation_mm = 0`.
  - `GET /plots/{id}/status` and the web decision card (incl. the rainfed variant): E9 (docs/10).
  - Scenario fixtures A/E and `/dev/scenarios`: E16. SLI "error de humedad": E11.

## Constraints
- ADR-0002 (hexagonal; cross-module use through the other module's `application` services and
  ports, never its adapters, as `weather/application/query_weather.py` does), ADR-0003, ADR-0009,
  ADR-0012, ADR-0022, ADR-0023, docs/04 conventions (problem+json, 404 across orgs), docs/09.
- Reuse: `farms.application.manage_plots.resolve_plot_access`, farms ports
  (`CropCycleRepository.get_active_for_plot`, `CropRepository`), `WeatherRepository.list_daily`,
  telemetry `query_readings` pattern + `CalibrationRepository.get_latest_valid_at`, weather job
  fan-out (`_defer_cell_job` lock/queueing_lock), the weather dev-jobs router.
- Domain is pure (floats, no I/O); Decimal only at the ORM boundary, as in farms/weather.
- No new dependencies. Ponytail full. CodeGraph first. Library docs via `find-docs`. English code.

## Route and checks
- Planning and orchestration: this Claude Code session (Opus 5.5). Writers never plan (owner
  2026-09-26).
- TDD: on (owner decision 2026-09-22, `AGENTS.md`), runner `uv run pytest` (server/). Owner rule
  for E6/E7 (2026-09-26): RED and GREEN are observed with targeted runs
  (`uv run pytest path::test`); the full suite runs once implementation is done, and failures are
  re-run targeted. Fast checks per commit: `uv run ruff check`, `uv run ruff format --check`,
  `uv run mypy`, `uv run lint-imports`.
- Test DB: own container `techcamp-e6-db`,
  `DATABASE_URL=postgresql+asyncpg://techcamp:techcamp@localhost:5436/techcamp`.
- Writers through Herdr (owner 2026-09-26): `agy` first — fastest and most capable, the default
  writer (parent runs RDD on its commits); `opencode` only when a mid-to-high unit needs a bit
  more (runs its own RDD); `odd-worker` only for complex work. One session per task group.
- Triggers fired: mapping (4+ files: one Sonnet explorer, 2026-09-26), writer (every unit touches
  2+ non-trivial files).
- Skills forwarded: `fastapi`, `pydantic`, `find-docs`, `work-unit-commits`, `chained-pr`,
  `systematic-debugging`, ponytail.
- Split rule (owner 2026-09-26): units over ~300 lines split into a/b sub-units, one commit
  each, as in E5; sub-units that belong to one functionality share one writer session.
- Delivery: `stacked-to-main`, about 400 authored lines per PR, merged in order. Forecast ≈ 1,700
  authored lines (T1 450, T2 300, T3 450, T4 250, T5 250).
- RDD: on (global). One `gentle-ai review assess --committed-only` per work-unit commit; first
  boundary is the branch point `a899aa6`.

## Decisions
- Initial depletion: the balance starts at `Dr = 0` (soil at field capacity) on the cycle's
  `sown_on`, or on the first day computed when the previous day's row is missing (FAO-56 ch. 8
  initialisation). A plot without an active cycle carries no balance; rainfed plots without a cycle
  still get the `delay_sowing` check (docs/06 §5 flowchart).
- Kc: only `development` is interpolated (docs/06 §5 table); `late` uses its own stage Kc; days past
  the cycle length use the `late` Kc.
- A plot whose soil profile lacks θFC, θWP or `root_depth_cm` (Zr is manual; SoilGrids never sets
  it) gets no balance and no recommendation for the day; the job logs it. Docs are silent here:
  recorded in docs/06 §5 with T3. Open question for the agronomist (docs/README pending
  validations).
- One job computes balance and recommendation together (docs/06 §5 flowchart); the dev route is
  `irrigation:run`, and docs/04:181 drops the separate `water-balance` name (T4).
- Weather: ET0 and rain for the day from the cell's observed row, else its forecast row; 48 h and
  7-day forecast sums from forecast rows. Weather older than 24 h marks low confidence in the
  `rationale` (docs/06 §6, E5 handoff).
- Water-balance status threshold: `watch` applies when `0.8 * RAW <= Dr < RAW`; below `0.8 * RAW`
  is `ok`. At or above `RAW`, irrigated plots report `irrigate` and rainfed plots report `stress`
  (rainfed never reports `irrigate`, docs/04:75; `stress` when `Dr > RAW`, docs/04:75, ADR-0022).
- Sensor representative depth tolerance: single sensor near `Zr/2` is evaluated with tolerance
  `0.15 * Zr` (`ZR_HALF_TOLERANCE_RATIO = 0.15`), marked with ponytail as pending agronomic
  validation (docs/06 §5).
- T2 persistence: `water_balance_daily` and `irrigation_recommendation` are plain tables (no
  hypertable). Job-side writes and previous-day read are org-agnostic
  (`CropCycleRepository.get_active_for_plot` pattern); user reads filter by `plot.org_id`.

## Tasks
- [x] T1 Domain FAO-56 rules, pure, with FAO-56 numeric examples (docs/06 §5,
  ADR-0009/0022/0023). Writer: agy, one session for T1a–T1b, one commit each.
  - [x] T1a Balance math: Kc per day (development interpolated), ETc, TAW, adjusted p, RAW,
    θ_stress, Pe, Dr model/observed, sensor weight K and assimilation.
  - [x] T1b Decision rules: recommendation kinds, depth and minutes, rainfed advice table,
    water-balance status, rationale.
  - Evidence (agy): T1a `b02c59d` — RED `test_kc_initial_stage_returns_initial_kc`
    (ModuleNotFoundError: techcamp.irrigation.domain.models); T1b `bb9833a` — RED
    `test_decide_recommendation_irrigated_irrigate_depth_and_minutes` (ImportError:
    WATCH_THRESHOLD_RATIO). Checks: `pytest tests/irrigation` 26 → 36 passed, ruff, format, mypy,
    lint-imports green. Both REDs are collection errors of a new module, the weakest RED form.
- [x] T2 `water_balance_daily` + `irrigation_recommendation` tables, migration (chained from
  `b7e2c9a41d38`), ORM, org-filtered repositories (docs/03:193-215, docs/09). Writer: agy.
  - Evidence (agy): `e2ec0db`, migration `d8a2f1c4e9b7`; RED
    `test_upsert_and_read_back_water_balance` (ModuleNotFoundError: irrigation.adapters.orm);
    `pytest tests/irrigation` 42 passed, ruff, format, mypy, lint-imports green. Plain tables
    (docs/03:391).
- [ ] T3 Daily balance use case per plot (docs/06 §5, ADR-0022). Writer: agy, one session for
  T3a–T3b, one commit each.
  - [ ] T3a Gather cycle/crop/stages, soil (read on the farms port), plot system, cell weather →
    compute and persist balance + recommendation with K = 0; docs/06 §5 note on missing soil
    data.
  - [ ] T3b Representative sensor + active calibration → K and assimilation, `without_sensor`
    flag.
- [ ] T4 Daily job 04:30 America/Bogota with per-plot fan-out, `POST /dev/jobs/irrigation:run
  {day?}`, docs/04:181 update (docs/06 §5, ADR-0012). Writer: agy.
- [ ] T5 `GET /plots/{id}/irrigation/recommendation?day=` and `GET /plots/{id}/water-balance`,
  plot access + 404 across orgs, response shapes recorded in docs/04 (docs/04:105-109). Writer:
  agy.
- [ ] T6 Full suite + checks, delivery slices (chained PRs).

## Acceptance criteria
- FAO-56 numeric examples pass for TAW, RAW with adjusted p, ETc and the daily Dr update.
- A plot with an irrigation system gets `irrigate` with depth and minutes when Dr ≥ RAW and no
  48 h rain covers it; `postpone` when it does; `not_needed` otherwise; `no_kc` for `kc_source =
  none`.
- A rainfed plot gets `rainfed` with no depth/minutes and the advice codes of docs/06 §5.
- Sensor assimilation applies only with a representative, field-calibrated sensor with a reading
  in 24 h; otherwise K = 0 and the rationale says "sin sensor".
- Every read is org-scoped; another org's plot returns 404.

## Progress
- 2026-09-26: worktree `e6-irrigation` on `feat/e6-irrigation` from `main` @ `a899aa6`; docs
  reviewed (docs/01, 03, 04, 06 §3/§5/§6, 10, ADR-0009/0022/0023); mapping done; feature doc
  created.

## Review (RDD)
- `f8ba925` feature doc: passive, boundary advanced.
- T1 `f8ba925..bb9833a` (1,190 lines, medium, slice budget reached): lineage
  `review-4d3bbc733433ca8a`, lens review-reliability, approved, acknowledged (authority burned).
  5 non-blocking findings (4 WARNING, 1 SUGGESTION) → #94. Boundary: `bb9833a`.
- T2 `bb9833a..e2ec0db` (853 lines, medium): lineage `review-edbd90727cd79f30`, review-reliability,
  approved, acknowledged. 6 non-blocking (4 WARNING, 2 SUGGESTION) → #96;
  R3-repo-commits-non-atomic folded into T3a (atomic balance + recommendation write).
  Boundary: `e2ec0db`.

## Next step
T3a–T3b via agy (one session).
