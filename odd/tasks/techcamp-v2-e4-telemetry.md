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
- TDD: on (owner decision 2026-09-22, `AGENTS.md`). RED → GREEN → REFACTOR. Runners: `uv run pytest` (server/), `npm test -- --run` (web/).
- Other checks: server `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`; web `npm run lint`, `npm run typecheck`, `npm run build`, `npm run size`.
- Route: delegated direct. Sonnet 5 only: `sonnet-high` for technical tasks, standard Sonnet for exploration and easy tasks. Triggers fired: mapping (10+ docs, mapped by one standard Sonnet explorer), writer (every task touches 2+ non-trivial files), preparation.
- Skills forwarded: `fastapi`, `pydantic`, `tdd`, `find-docs`, `impeccable` (web), `work-unit-commits`, `chained-pr`.
- Delivery: `stacked-to-main`, about 400 authored lines per PR, merged in order. Forecast ≈ 10,000–10,500 authored code lines (calibrated on E3: forecast 2,400, actual ~10,700). Branch `feat/e4-telemetry` from `main` @ `e79d542`.
- RDD: on (global). One `gentle-ai review assess --committed-only` per work-unit commit; first boundary is the branch point `e79d542`.

## Decisions
- Alert evaluation during ingest is a no-op hook until E7 (dependency direction E4 → E7).
- The simulator covers one basic node; the scenario machinery is E16.

## Tasks
- [ ] T1 Schema: `node`/`sensor`/`calibration`/`reading` migrations, hypertable, compression, continuous aggregates, ORM, repositories filtered by `org_id` — route: delegated (sonnet-high) — forecast ~850
- [ ] T2 Pure domain: calibration methods (linear, two_point, polynomial), uplink payload validation, quality rules — route: delegated (sonnet-high) — forecast ~600
- [ ] T3 Node API: claim (one-time password), list, patch, rotate, health, sensors, calibrations; org isolation test — route: delegated (sonnet-high) — forecast ~1,200
- [ ] T4 Ingestor: `aiomqtt` subscriber, batching, idempotent insert, status/LWT, `NOTIFY`, `ingestor` compose service — route: delegated (sonnet-high) — forecast ~1,000
- [ ] T5 Readings query: `raw|hour|day`, 2-day and 60-day limits — route: delegated (sonnet-high) — forecast ~600
- [ ] T6 SSE stream: `LISTEN` fan-out, farm filter + org check, keepalive, `Last-Event-ID` — route: delegated (sonnet-high) — forecast ~700
- [ ] T7 Recalibration job: procrastinate setup, `worker` compose service, recompute `value` — route: delegated (sonnet-high) — forecast ~500
- [ ] T8 Basic simulator CLI: claim or create node, backfill N days, 5 s live loop, raw ADC values — route: delegated (sonnet) — forecast ~550
- [ ] T9 Web nodes: QR scan + manual fallback, claim sheet (password once), node list + health, calibration form (via `impeccable`) — route: delegated (sonnet-high) — forecast ~2,100
- [ ] T10 Web live reading on plot detail via `EventSource` — route: delegated (sonnet) — forecast ~550
- Review-fix rounds (T*b) reserved: forecast ~1,800

## Review (RDD)
- Boundary: `e79d542`.

## Acceptance criteria
- The simulator publishes over MQTT, the ingestor stores calibrated `reading` rows (raw and calibrated), duplicates are ignored.
- `curl -N /api/v1/stream?farm_id=…` shows `reading` events live; other orgs get 404.
- Recalibration recomputes `value` from `raw_value` forward.
- Node claim by QR/code works from the web; health and calibration visible.
- All server and web checks green.

## Progress / evidence
- 2026-09-24: branch `feat/e4-telemetry` created from `main` @ `e79d542`; feature doc created.

## Next step
T1 schema.
