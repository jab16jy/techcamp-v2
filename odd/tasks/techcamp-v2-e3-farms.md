# TechCamp v2 — E3 Farms and plots

## Objective
Deliver epic E3 from `docs/10-dag.md`: create a farm and a plot by drawing its polygon, soil profile autofill, crop catalog with Kc, and crop cycles.

## Why
E3 is on the critical path (E2 → E3 → E4 → E6 → E9) and unblocks E4, E5 and E8. Irrigation math (E6) needs plot boundary, irrigation system, soil water limits and crop Kc from this module.

## Scope
- In (`farms` module + web):
  - `farm`, `plot` (PostGIS Polygon SRID 4326, `area_ha` derived from `boundary`, irrigated or rainfed, per ADR-0023), `soil_profile`, `crop` + `crop_stage`, `crop_cycle` (docs/03-modelo-datos.md:85-135, 445-455, 473-474, 489).
  - Endpoints from docs/04-api.md:43-56 except `plot_baseline`.
  - Soil autofill `POST /plots/{plot_id}/soil:autofill` (RF-03) with the FAO-56 Table 19 texture fallback.
  - Web: plots tab with farm/plot creation, polygon drawing with Leaflet, lazy loaded (docs/05-arquitectura.md:179, docs/07 §plots), soil and cycle forms.
- Out:
  - `plot_baseline`, owned by E11 (docs/10-dag.md:74, docs/11-metricas.md:35).
  - `weather_cell_id` assignment, owned by E5: the column stays nullable until then.
  - Offline base-map tiles (RF-18, deferred).
  - Offline sync of these entities, owned by E8 / ADR-0013.
  - Crop suitability scoring (ADR-0011).

## Constraints
- ADR-0002 (hexagonal, import-linter), ADR-0003 (PostGIS), ADR-0006 + docs/07 (UI only through `impeccable`), ADR-0021 (seminar profile), ADR-0023 (irrigated/rainfed), docs/04 conventions (problem+json, 404 across orgs, `Idempotency-Key` on POST), docs/09 (every repository filters by `org_id`).
- Reuse E2 patterns: `shared/db.py` `Base`/`SessionDep`, `shared/ids.py` `uuid7`, `shared/errors.py` `ProblemError`, `identity/application/resolve_org_access.py`, `identity/adapters/api/deps.py` `CurrentUserId`, the real-Postgres fixtures in `server/tests/conftest.py`.
- Ponytail: shortest diff that works; a port only for external I/O (SoilGrids) or when two implementations exist.
- Library docs via `find-docs` (ctx7). English code; Spanish UI copy only where docs/07 requires it.

## Route and checks
- TDD: on (owner decision 2026-09-22, `AGENTS.md`). RED → GREEN → REFACTOR, failing test observed first. Runners: `uv run pytest` (server/), `npm test -- --run` (web/).
- Other checks: server `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`; web `npm run lint`, `npm run typecheck`, `npm run build`, `npm run size`.
- Route: delegated direct, one `sonnet-high` writer per task. Triggers fired: mapping (10+ docs, mapped by one sonnet-high explorer), writer (every task touches 2+ non-trivial files), preparation.
- Delivery: `stacked-to-main` (repo policy, `AGENTS.md`), about 400 authored lines per PR, merged in order. Forecast ≈ 2,400 authored lines, so the epic ships as several PRs. Branch `feat/e3-farms` from `main` @ `4684262`; `db7ae3f` ignores the local CodeGraph index (RDD declined by owner for that candidate).
- RDD: on (global). One `gentle-ai review assess --committed-only` per work-unit commit; first boundary is the branch point `4684262`.

## Decisions
- `crop_cycle` is in scope: the E3 node in docs/10-dag.md:14 names "ciclos".
- `plot_baseline` is out of scope: E11 owns the enrollment survey (docs/10-dag.md:74).
- `area_ha` = `ST_Area(boundary::geography) / 10000`, computed in the database (generated column or insert expression), never supplied by the client. Geodesic area avoids choosing a projected SRID.
- SoilGrids in the seminar profile (owner, 2026-09-23): real ISRIC HTTP adapter for production; the seminar profile serves a recorded SoilGrids response, offline and deterministic like weather. T5 adds the row to ADR-0021 in the same work unit.

## Tasks
- [x] T1 Farm and plot schema: `geoalchemy2` dependency, ORM rows, Alembic migration (GiST index, irrigation `CHECK`, `area_ha`), domain rules (default efficiency by system), repositories filtered by `org_id` — route: delegated — forecast ~300 — actual ~623
- [ ] T2 Farm and plot endpoints: `GET/POST /farms`, `PATCH /farms/{id}`, `GET/POST /farms/{id}/plots`, `PATCH /plots/{id}`; GeoJSON Polygon validation; org isolation test; T1 review follow-ups (see Review) — route: delegated — forecast ~350
- [ ] T3 Crop catalog: `crop` + `crop_stage` migration, seed with FAO-56 Table 12 Kc and `kc_source`, `GET /crops` — route: delegated — forecast ~300
- [ ] T4 Soil profile: `soil_profile` migration, `PUT /plots/{id}/soil`, FAO-56 Table 19 texture fallback — route: delegated — forecast ~250
- [ ] T5 Soil autofill: SoilGrids port + adapter + test double, `POST /plots/{id}/soil:autofill` (seminar: recorded fixture; ADR-0021 row) — route: delegated — forecast ~250
- [ ] T6 Crop cycles: `crop_cycle` migration (one active cycle per plot), `POST /plots/{id}/cycles`, `PATCH /cycles/{id}` — route: delegated — forecast ~250
- [ ] T7 Web data layer and plots route: API client, farm/plot list in the plots tab (via `impeccable`) — route: delegated — forecast ~250
- [ ] T8 Web plot creation: lazy-loaded Leaflet map, draw polygon, farm and plot forms (via `impeccable`) — route: delegated — forecast ~350
- [ ] T9 Web soil and cycle: soil autofill/edit and crop cycle forms with Kc shown (via `impeccable`) — route: delegated — forecast ~300

## Review (RDD)
- `4684262..cacf2ab` (`.gitignore`, this doc): owner granted; lineage `review-d8ec7c69794659a0`, reliability lens, 0 findings, approved and acknowledged (authority burned).
- T1 `cacf2ab..1d44cf8` (medium, `server/migrations/env.py`, 668 lines, `slice_budget_reached`): owner granted; lineage `review-6371844322cab237`, reliability lens, approved and acknowledged. 0 blocking; follow-ups folded into T2:
  - WARNING: `plot.org_id` and `plot.farm_id` are independent FKs, so a plot can point to another org's farm. Enforce with a composite FK `(farm_id, org_id)` → unique `farm(id, org_id)`.
  - WARNING: `list_for_org` / `list_for_farm` untested and unordered; add tests and a deterministic `ORDER BY`.
  - SUGGESTION: range `CHECK`s (0 < efficiency ≤ 1, flow > 0); index on `plot(org_id, farm_id)`; assert WKT round-trip and the rainfed `system_flow_lph` `CHECK` in tests.
- Owner standing decision (2026-09-23): consent is granted by default for new-feature candidates. Reviewed boundary is now `1d44cf8`.
- Local only: `.impeccable/surfaces/config.local.json` is listed in `.git/info/exclude` so RDD candidate selection ignores it.

## Acceptance criteria
- [ ] A user creates a farm and a plot from a drawn polygon; `area_ha` comes from the geometry.
- [ ] Rainfed plots reject irrigation efficiency and flow (DB `CHECK` and 422).
- [ ] Soil autofill fills θFC/θWP from SoilGrids, or from the FAO-56 texture table with `source = fao56_texture`.
- [ ] `GET /crops` returns stages, Kc and `kc_source`.
- [ ] A plot holds at most one active crop cycle.
- [ ] Cross-org access to farms, plots, soil and cycles returns 404.
- [ ] All server and web checks green locally and in CI.

## Progress / evidence
- 2026-09-23: CodeGraph initialized; branch created; requirements mapped by a sonnet-high explorer (docs/01, 03, 04, 05, 07, 09, 10, ADR-0011/0021/0023).
- 2026-09-23: T1 done by a delegated `sonnet-high` writer. `farms` module: `domain/models.py`
  (`IrrigationSystem`, `DEFAULT_IRRIGATION_EFFICIENCY`, `default_efficiency_for`,
  `ensure_rainfed_has_no_irrigation`, `Farm`/`Plot` dataclasses), `domain/errors.py`
  (`RainfedPlotHasIrrigationError`), `adapters/orm.py` (`FarmRow`, `PlotRow`), `adapters/repositories.py`
  (`SqlAlchemyFarmRepository`, `SqlAlchemyPlotRepository`, read-only, org-scoped). Migration
  `9098dc0927a3` (chained off `b358c1328b49`): `farm` (`ix_farm_org_id`), `plot` (GiST on
  `boundary`, `ck_plot_irrigation_system`, `ck_plot_rainfed_has_no_irrigation`).
  - Decisions: `area_ha` is a DB **generated column** (`Computed`, `STORED`) —
    `ST_Area(geometry)`/`geography(geometry)` are `IMMUTABLE` in this PostGIS build (verified via
    `pg_proc.provolatile`), so Postgres accepts the generated-column expression; no insert-time
    fallback needed. Geometry columns are typed `Mapped[Any]` in the ORM (GeoAlchemy2 accepts a
    WKT/EWKT `str` on write, returns `WKBElement` on read); repositories select
    `ST_AsText(...)` so the domain only ever sees WKT `str`. No `application/ports.py` for
    farms yet: T1 has no use case depending on repository behavior through an abstraction
    (ponytail: a port only for external I/O or two real implementations). Added
    `tests/farms/__init__.py` and `tests/identity/__init__.py` — same-named test modules
    (`test_domain_models.py`, `test_repositories.py`) in different module dirs collided under
    pytest's rootless import without package markers.
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/).
    RED observed by temporarily removing the four new implementation files and running
    `uv run pytest -q tests/farms`: 2 collection errors, `ModuleNotFoundError: No module named
    'techcamp.farms.domain.errors'` / `'techcamp.farms.adapters.orm'`. Restored the files: GREEN,
    `16 passed in 1.82s`. REFACTOR: none needed beyond `ruff format`.
  - Verification (server/): `uv run pytest -q` → `45 passed, 2 warnings in 3.33s`; `uv run ruff
    check .` → `All checks passed!`; `uv run ruff format --check .` → `88 files already
    formatted` (2 files reformatted first pass); `uv run mypy` → `Success: no issues found in 69
    source files`; `uv run lint-imports` → `Hexagonal layers per module KEPT, 1 kept, 0 broken`;
    `uv run alembic upgrade head` → applied `b358c1328b49` then `9098dc0927a3`; `uv run alembic
    downgrade -1` → reverted `9098dc0927a3`; `uv run alembic upgrade head` → reapplied clean.
  - Postgres for tests was not running; started with `uvx podman-compose -f infra/compose.yaml
    --profile seminar up -d postgres` after removing a stale `infra_postgres_1` container left
    over from a deleted `e2-identity` worktree (its bind-mounted `init-extensions.sql` path no
    longer existed).
  - Commit: `fe57f6f6f1ae36d2c887f8daa338c1b135a03392` — `feat(farms): add farm and plot schema
    with org-scoped repositories`. Authored lines (`git diff --stat 4684262..HEAD -- . ':!server/uv.lock'`,
    this commit's files): 623 insertions, 0 deletions across 10 new files + 2 one-line edits
    (`server/migrations/env.py`, `server/pyproject.toml`).
  - Doc gap: docs/03-modelo-datos.md:89 models `farm.municipality_code` as `FK "DIVIPOLA"`, but
    no `municipality` table exists in this repo yet (not migrated from v1; that's a later epic
    per docs/03's migration table). Implemented as plain `text`, noted in code and here; owner
    should confirm before any epic adds the `municipality` table and a real FK.

## Next step
T2 (delegated writer): farm/plot endpoints.
