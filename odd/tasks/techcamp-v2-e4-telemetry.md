# TechCamp v2 — E4 Telemetry

## Objective
Deliver epic E4 from `docs/10-dag.md:67`: a basic node simulator publishes, and its readings land calibrated in the database and live over SSE. Node scope (docs/10-dag.md:15): broker, ingestor, QR claim, calibration, simulator.

## Why
E4 is on the critical path (E2 → E3 → E4 → E6 → E9) and unblocks E6 (irrigation), E7 (alerts) and E16 (scenarios). The water balance and alert rules consume calibrated readings from this module.

## Scope
- In (`telemetry` module + ingestor + simulator + web):
  - `node`, `sensor`, `calibration` (versioned, never edited in place), `reading` (hypertable, `UNIQUE(sensor_id,time)`, compression after 7 days, `segmentby=sensor_id`), continuous aggregates `reading_hourly` and `reading_daily` (docs/03-modelo-datos.md:136-174, 372-385, 459-475).
  - MQTT contract `tc/v1/{node_id}/up|status|down`, uplink `{v,seq,ts,fw,m}` (docs/04-api.md:192-224, ADR-0004).
  - Ingest pipeline (docs/06-diseno-detallado.md §1): validate → map `channel_key` → calibrate → `quality` → batch insert (500 msgs or 1 s) → `ON CONFLICT DO NOTHING` → `node.last_seen_at`/`status` → `NOTIFY plot_events`.
  - Node endpoints (docs/04-api.md:79-93): claim, list, patch, rotate credentials, health, sensors, calibrations.
  - `GET /plots/{plot_id}/readings` with `raw|hour|day` resolution and range limits (docs/04-api.md:94-96).
  - SSE `GET /api/v1/stream?farm_id=` with `LISTEN` fan-out, `Last-Event-ID`, 20 s keepalive (docs/04-api.md:180-189, ADR-0015).
  - Recalibration job on procrastinate (ADR-0012): recompute `value` from `raw_value` forward.
  - Basic simulator CLI (docs/06-diseno-detallado.md §10, single-node publish path only): raw ADC values, backfill, live loop, own calibration.
  - Web `features/nodes` (docs/07-frontend-design-system.md:67, 127-128): QR claim, calibration, health; live reading on plot detail.
- Out:
  - E13 LoRaWAN/ChirpStack (docs/10-dag.md:77).
  - E16 A–E scenario library, weather fixtures, `expected` e2e checks, fault injection (docs/10-dag.md:76).
  - E7 alert rules: the "evaluate hot rules" ingest step is a no-op until E7 (E7 depends on E4).
  - E9 home screen; real firmware.
  - Mosquitto Dynamic Security (production). Seminar broker is anonymous (ADR-0021); credentials are generated and hashed but not enforced.

## Constraints
- ADR-0002 (hexagonal, import-linter), ADR-0003 (TimescaleDB), ADR-0004 (MQTT), ADR-0012 (jobs in Postgres), ADR-0015 (SSE), ADR-0021 (seminar profile), docs/04 conventions (problem+json, 404 across orgs, `Idempotency-Key` on POST), docs/09 (every repository filters by `org_id`).
- Reuse E2/E3 patterns: `farms` module layout, `shared/db.py`, `shared/ids.py` `uuid7`, `shared/errors.py` `ProblemError`, `identity/application/resolve_org_access.py`, `CurrentUserId`, real-Postgres fixtures; web `openapi-fetch` + TanStack Query (`web/src/lib/api`, `features/plots/api/plotsApi.ts`).
- Web: E1 design as-is, via `impeccable`; gaps flagged, not invented.
- Ponytail: shortest diff that works; new deps only `aiomqtt`, `procrastinate`; native `BarcodeDetector` with manual code fallback before any QR library.
- Library docs via `find-docs` (ctx7). English code.

## Route and checks
- TDD: on (owner decision 2026-09-22, `AGENTS.md`), run by the ODD workflow itself; no `tdd` skill (owner, 2026-09-25). RED → GREEN → REFACTOR. Runners: `uv run pytest` (server/), `npm test -- --run` (web/).
- Other checks: server `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`; web `npm run lint`, `npm run typecheck`, `npm run build`, `npm run size`.
- Route: delegated direct. Sonnet 5 only: `sonnet-high` for technical tasks, standard Sonnet for exploration and easy tasks. Triggers fired: mapping (10+ docs, mapped by one standard Sonnet explorer), writer (every task touches 2+ non-trivial files), preparation.
- Skills forwarded: `fastapi`, `pydantic`, `find-docs`, `impeccable` (web), `work-unit-commits`, `chained-pr`.
- Delivery: `stacked-to-main`, about 400 authored lines per PR, merged in order. Forecast ≈ 10,000–10,500 authored code lines (calibrated on E3: forecast 2,400, actual ~10,700). Branch `feat/e4-telemetry` from `main` @ `e79d542`.
- RDD: on (global). One `gentle-ai review assess --committed-only` per work-unit commit; first boundary is the branch point `e79d542`.

## Decisions
- Readings query (T5): docs/04:93 uses one `[t, value]` point shape for every resolution, so `hour`/`day` points carry the bucket average. Readings with a null `value` and buckets without a calibrated average are excluded.
- Ingest (T4, recorded in docs/06 §1): out-of-range (quality 2) wins over timestamp-corrected (1); a reading with no valid calibration is stored raw with `value` null. Only claimed nodes are ingested. `ingest_uplinks` resolves `farm_id` through the farms `PlotRepository` for SSE fan-out.
- Claim (T3): unknown claim code → 404; already claimed → 409; PATCH `plot_id` in another org → 422. `GET /nodes` takes the required `org_id` query parameter like `GET /farms` (docs/04 omits it). Sensors come pre-provisioned with the unclaimed node, and the claim leaves them untouched: docs/06 §2 mentions a "hardware model" that the schema doesn't have (doc gap). `battery_v`/`rssi` in health return null until T4 turns the `bat`/`rssi` channels into readings. `completeness_24h` = distinct reading timestamps in 24 h ÷ (86400 / `interval_s`), capped at 1 (docs/11). The one-time password uses stdlib `scrypt` in `shared/credentials.py`. `Idempotency-Key` stays deferred, as in E3.
- Alert evaluation during ingest is a no-op hook until E7 (dependency direction E4 → E7).
- The simulator covers one basic node; the scenario machinery is E16.

## Tasks
- [x] T1 Schema: `node`/`sensor`/`calibration`/`reading` migrations, hypertable, compression, continuous aggregates, ORM, repositories filtered by `org_id` — route: delegated (sonnet-high) — forecast ~850 — actual 1,063 (`9b5dd96`)
- [x] T1b Unclaimed nodes: `node.org_id`/`plot_id` nullable with all-or-nothing ownership CHECK (docs/06 §2: claim assigns org and plot) — route: delegated (sonnet) — forecast ~100 — actual 152 (`1df9981`)
- [x] T1c Fix #33: org-scope `add_version`, `version DESC` tiebreaker, isolate status CHECK test, idempotent Timescale DDL, declare `ix_reading_sensor_time` in ORM — route: delegated (sonnet-high) — forecast ~150 — actual 170 (`8ba6fa7`). Last immediate follow-up fix: from now on non-blocking findings stay in the issue tracker for later (owner, 2026-09-24)
- [x] T2 Pure domain: calibration methods (linear, two_point, polynomial), uplink payload validation, quality rules — route: delegated (sonnet-high) — forecast ~600 — actual 411 (`04fe5b2`)
- [x] T3 Node API: claim (one-time password), list, patch, rotate, health, sensors, calibrations; org isolation test — route: delegated (sonnet-high) — forecast ~1,200 — actual 1,535 (`d11c578`)
- [x] T4 Ingestor: `aiomqtt` subscriber, batching, idempotent insert, status/LWT, `NOTIFY`, `ingestor` compose service; discard messages with `ts` older than 30 days (docs/06 §1); decide quality precedence when a reading is both timestamp-corrected and out of range — route: delegated (sonnet-high) — forecast ~1,000 — actual 1,464 (`3c116c2`) + review correction 86 (`8e3038c`)
- [x] T5 Readings query: `raw|hour|day`, 2-day and 60-day limits — route: delegated (sonnet-high) — forecast ~600 — actual 735 (`511480b`)
- [x] T6 SSE stream: `LISTEN` fan-out, farm filter + org check, keepalive, `Last-Event-ID` — route: delegated (sonnet-high, essentials-only brief) — forecast ~700 — actual 617 (`7e50442`)
- [x] T6b Fix T6 review findings (CRITICAL `R3-stream-holds-db-session` plus all WARNING/SUGGESTION, owner 2026-09-25) — route: delegated (sonnet-high) — forecast ~250 — actual 441 (`c6e7c69`)
- [x] T7 Recalibration job: procrastinate setup, `worker` compose service, recompute `value` — route: delegated (sonnet-high) — forecast ~500 — actual 736 (`ab7a665`, excluding `uv.lock`)
- [ ] T8 Basic simulator CLI: claim or create node, backfill N days, 5 s live loop, raw ADC values — route: delegated (sonnet) — forecast ~550
- [ ] T9 Web nodes: QR scan + manual fallback, claim sheet (password once), node list + health, calibration form (via `impeccable`) — route: delegated (sonnet-high) — forecast ~2,100
- [ ] T10 Web live reading on plot detail via `EventSource` — route: delegated (sonnet) — forecast ~550
- Review-fix rounds (T*b) reserved: forecast ~1,800

## Review (RDD)
- Boundary: `e79d542`.
- `fe85ae7` (feature doc): assessed passive, no review; boundary → `fe85ae7`.
- T1 + T1b (`fe85ae7..1df9981`, 1,215 lines): assessed medium, `slice_budget_reached`; standing grant applied; lineage `review-99634a0063c7e89e`, one reliability lens, APPROVED and acknowledged. 4 WARNING + 1 SUGGESTION, non-blocking → issue #33 (fixed by T1c). Boundary → `1df9981`.
- `43d2abe` (doc): passive, boundary → `43d2abe`.
- T1c `8ba6fa7`: medium, `under_budget` (170 lines); pending in the next slice.
- T1c + T2 (`43d2abe..04fe5b2`, 588 lines): medium, `slice_budget_reached`; standing grant applied; lineage `review-d326a92df3ebed8d`, one reliability lens, APPROVED and acknowledged. 2 WARNING + 2 SUGGESTION, non-blocking → issue #34, deferred (no immediate fix task). Boundary → `04fe5b2`.
- `e231efd` (doc): passive, boundary → `e231efd`.
- T3 (`e231efd..d11c578`, 1,535 lines): medium, `slice_budget_reached`; standing grant applied; lineage `review-6de842485f32f243`, one reliability lens, APPROVED and acknowledged. 4 WARNING + 2 SUGGESTION, non-blocking → issue #35, deferred. Boundary → `d11c578`.
- `a3ae810` (doc): passive, boundary → `a3ae810`.
- T4 (`a3ae810..3c116c2`, 1,464 lines): medium, `slice_budget_reached`; standing grant applied; lineage `review-3382490048a23b13`. CRITICAL `R3-flush-failure-kills-ingestor` (a flush exception killed `run()` and lost the drained QoS-1 batch; no compose restart policy) → correction plan of 150 lines → bounded correction `8e3038c` (86 lines: `_flush_with_retry` requeues the batch, `restart: unless-stopped`) → provider targeted validation APPROVED and acknowledged. 3 WARNING + 1 SUGGESTION, non-blocking → issue #36, deferred. Boundary → `8e3038c`.
- `5285624` (doc): passive, boundary → `5285624`.
- T5 (`5285624..511480b`, 735 lines): medium, `slice_budget_reached`; standing grant applied; lineage `review-c462c8ae0e2f8bca`, one reliability lens, APPROVED and acknowledged. 1 WARNING + 2 SUGGESTION, non-blocking → issue #37, deferred. Boundary → `511480b`.
- T6 (`f9e9e1f..7e50442`, 626 lines): medium, `slice_budget_reached`; standing grant applied; lineage `review-8d4dc4757b571a56`, one reliability lens → `correction_required`: CRITICAL `R3-stream-holds-db-session` (the stream holds a pooled `AsyncSession` for the whole connection; refuter inconclusive), WARNING `R3-listener-no-reconnect`, `R3-lifespan-coupling`, `R3-partial-payload-unhandled`, SUGGESTION `R3-id-assertion-shared-channel`. The provider-issued continuation then stopped terminally with `corrupted_or_unverifiable_authority` (gentle-ai 3.7.0, repair unsupported); the owner chose not to report it. The lineage stays as is; T6b fixes every finding in a new commit that gets a fresh review.
- `40a57ec` (doc) + T6b `c6e7c69`, reviewed together with T6 (`0e2d628..c6e7c69`, 1,006 lines): medium, `slice_budget_reached`; standing grant applied (includes the T6 feat); lineage `review-30e475c011e65303`, one reliability lens, APPROVED and acknowledged (authority burned); it confirms every T6 finding fixed. 2 WARNING + 1 SUGGESTION, non-blocking → issue #38, deferred. Boundary → `c6e7c69`.
- `57e3920` (doc): passive, boundary → `57e3920`.
- T7 (`57e3920..ab7a665`, 902 lines): medium, `slice_budget_reached`; standing grant applied; lineage `review-68ffa155134d67c5`, one reliability lens, APPROVED and acknowledged. 6 WARNING, non-blocking → issue #39, deferred. Boundary → `ab7a665`.

## Acceptance criteria
- The simulator publishes over MQTT, the ingestor stores calibrated `reading` rows (raw and calibrated), duplicates are ignored.
- `curl -N /api/v1/stream?farm_id=…` shows `reading` events live; other orgs get 404.
- Recalibration recomputes `value` from `raw_value` forward.
- Node claim by QR/code works from the web; health and calibration visible.
- All server and web checks green.

## Progress / evidence
- 2026-09-24: branch `feat/e4-telemetry` created from `main` @ `e79d542`; feature doc created.
- 2026-09-24: T1 done (`9b5dd96`): server pytest 227 passed, ruff, format, mypy, lint-imports green (writer). T1b (`1df9981`): pytest 234 passed, all server checks green (writer); parent spot check `uv run pytest tests/telemetry` 22 passed. Writer disclosed partial Read-before-CodeGraph during T1 exploration.
- 2026-09-25: T7 (`ab7a665`): RED observed only for the pure `recalibrate_reading` (`ImportError`); the DB-level job behaviors were written alongside their tests with no RED recorded — TDD DEVIATION, as in T3. pytest 371 passed, ruff, format, mypy, lint-imports green, compose YAML parses (writer); parent spot check `pytest tests/telemetry/test_jobs.py tests/telemetry/test_domain_models.py` 44 passed. Enqueue calls procrastinate's `procrastinate_defer_jobs_v1` over the same asyncpg session (same transaction as the calibration insert); UPDATE works on compressed chunks. Extra dependency `psycopg[binary]` (procrastinate needs libpq). Gap: `quality` stores two signals in one value (docs/03:174), so recomputation maps a previous non-OK quality to timestamp-corrected (#39). The writer recreated the local `techcamp` database (partial migration state) without asking. PENDING: running the `worker` service (no compose runtime here), covered by the end-to-end demo.
- 2026-09-25: T6b (`c6e7c69`): RED observed per finding (11 failing, including the pool-checkout test `assert 1 == 0` and `OSError` at lifespan boot); pytest 362 passed, ruff, format, mypy, lint-imports green (writer); parent spot check `pytest tests/telemetry/test_sse_stream.py tests/telemetry/test_stream_api.py` 24 passed. `Depends(scope="function")` does not reach `SessionDep`'s sub-dependency, so the stream runs its access check in its own short-lived session. Gap: a client that subscribes before the hub's first connect gets keepalives only until it connects (docs silent).
- 2026-09-25: T6 (`7e50442`): RED observed (`ModuleNotFoundError` for the stream module); pytest 351 passed, ruff, format, mypy, lint-imports green (writer); parent spot check `pytest tests/telemetry/test_sse_stream.py tests/telemetry/test_stream_api.py` 13 passed. FastAPI's native `EventSourceResponse` pings `: ping` every 15 s (hardcoded), so T6 uses `StreamingResponse` to match docs/04's `:keepalive` every 20 s. Vite's proxy streams SSE unbuffered (unchanged). Gaps: stream auth for `EventSource` (T10 decides), `Last-Event-ID` accepted but not replayed (docs silent).
- 2026-09-25: T5 (`511480b`): RED observed per behavior group; pytest 338 passed, ruff, format, mypy, lint-imports green (writer); parent spot check `pytest tests/telemetry/test_readings_api.py tests/telemetry/test_reading_repository.py` 14 passed.
- 2026-09-25: T4 (`3c116c2`): RED observed per behavior group; pytest 316 passed, ruff, format, mypy, lint-imports green (writer); parent spot check `pytest tests/telemetry/test_ingest.py tests/telemetry/test_ingestor.py` 23 passed. Correction `8e3038c`: pytest 318 passed (writer); parent spot check `test_ingestor.py` 10 passed. PENDING: compose validation (`podman-compose` is not installed on this machine; the writer only parsed the YAML) and a live-broker check of the `$share/ingestors/` subscription — both covered by the E4 end-to-end demo.
- 2026-09-24: T3 (`d11c578`): pytest 283 passed, ruff, format, mypy, lint-imports green (writer); parent spot check `pytest tests/telemetry/test_api.py` 20 passed. TDD DEVIATION: the writer wrote the tests alongside the implementation, so no RED was observed, which departs from the project TDD rule. Recorded here, not hidden; the next writers get a stricter RED-evidence requirement.
- 2026-09-24: T2 (`04fe5b2`): pytest 263 passed, ruff, format, mypy, lint-imports green (writer); parent spot check `pytest tests/telemetry/test_domain_models.py` 26 passed. Linear params use the documented names `scale`/`offset` (docs/03:465). Out-of-range rule only for `%` units: docs/06 §1 gives no other ranges.
- 2026-09-24: T1c (`8ba6fa7`, Refs #33): pytest 237 passed, ruff, format, mypy, lint-imports green (writer); parent spot check `pytest tests/telemetry` 25 passed. `add_version` org check lives in the repository (no application layer yet); `op.create_table` stays non-idempotent with a recovery comment.

## Next step
T8 (basic simulator CLI): claim or create a node, backfill N days, 5 s live loop, raw ADC values (docs/06 §10, single-node path). Review boundary `ab7a665`. Then T9–T10 (web), end-to-end demo (also validates compose, the `worker` service and the MQTT shared subscription), then stacked-to-main PRs.
