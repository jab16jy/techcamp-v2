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
- [x] T2 Farm and plot endpoints: `GET/POST /farms`, `PATCH /farms/{id}`, `GET/POST /farms/{id}/plots`, `PATCH /plots/{id}`; GeoJSON Polygon validation; org isolation test; T1 review follow-ups (see Review) — route: delegated — forecast ~350 — actual ~1519
- [x] T2b Fix T2 review follow-ups: 422 on explicit nulls, validate `technician_id` membership, default efficiency on system switch, coordinate bounds, missing API tests — route: delegated — forecast ~150 — actual ~445
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
  - WARNING: `plot.org_id` and `plot.farm_id` are independent FKs, so a plot can point to another org's farm. Enforce with a composite FK `(farm_id, org_id)` → unique `farm(id, org_id)`. **Resolved in T2**: migration `6628f7c0aa3b` adds `uq_farm_id_org_id` on `farm` and `fk_plot_farm_id_org_id` on `plot`; `test_plot_of_a_different_orgs_farm_is_rejected_by_the_database` covers it.
  - WARNING: `list_for_org` / `list_for_farm` untested and unordered; add tests and a deterministic `ORDER BY`. **Resolved in T2**: both repositories now `ORDER BY id` (uuid7 is time-ordered); `test_list_for_org_filters_by_org_and_orders_deterministically` and `test_list_for_farm_orders_deterministically`, plus empty-result tests for both.
  - SUGGESTION: range `CHECK`s (0 < efficiency ≤ 1, flow > 0); index on `plot(org_id, farm_id)`; assert WKT round-trip and the rainfed `system_flow_lph` `CHECK` in tests. **Resolved in T2**: migration `6628f7c0aa3b` adds `ck_plot_irrigation_efficiency_range`, `ck_plot_system_flow_positive`, `ix_plot_org_farm`; `test_out_of_range_efficiency_is_rejected_by_the_database`, `test_non_positive_flow_is_rejected_by_the_database`, `test_boundary_round_trips_as_wkt`.
- Owner standing decision (2026-09-23): consent is granted by default for new-feature candidates. Reviewed boundary is now `1d44cf8`.
- Whole branch `4684262..8f7c472` (stop-hook candidate, standing grant): lineage `review-276fecec5c5794cf`, approved and acknowledged; same five T1 findings, already resolved in T2.
- T2 `8f7c472..17c3943` (medium, new migration, 1588 lines, `slice_budget_reached`, standing grant): lineage `review-3bf23165586f9cfb`, reliability lens, approved and acknowledged. 0 blocking; follow-ups become task T2b:
  - WARNING: explicit JSON `null` in `PATCH /plots` (`boundary`, `irrigation_system`, `name`) and `PATCH /farms` (`name`) reaches the DB or `polygon_to_wkt` and returns 500 instead of 422. **Resolved in T2b**: `router._reject_explicit_null` (shared by `patch_farm`/`patch_plot`) raises `422` for an explicit null on a non-nullable field before either the DB write or the boundary conversion runs; nullable fields (`technician_id`, `irrigation_efficiency`, `system_flow_lph`) still accept null. Covered by `test_explicit_null_on_farm_name_is_422`, `test_explicit_null_on_non_nullable_plot_fields_is_422`, `test_explicit_null_on_technician_id_still_clears_it`.
  - WARNING: `technician_id` on `POST`/`PATCH /farms` is not checked to be a member of the farm's org; an unknown id gives an `IntegrityError` 500. **Resolved in T2b**: `manage_farms._ensure_valid_technician` requires `technician_id` to be a member of the org with a write role (owner or technician — `WRITE_ROLES`; docs/03 is silent on the exact role). Raises `InvalidTechnicianError` → `422`. Covered by `test_foreign_technician_id_is_422_not_500`, `test_technician_id_of_a_producer_is_422`, `test_technician_id_of_a_technician_is_accepted`.
  - SUGGESTION: `update_plot` does not apply the default efficiency when switching a rainfed plot to an irrigated system. **Resolved in T2b**: same rule as `create_plot`, applied in `manage_plots.update_plot`; `test_switching_a_plot_to_irrigated_without_efficiency_uses_the_default`.
  - SUGGESTION: GeoJSON coordinates accept out-of-range and non-finite values. **Resolved in T2b**: `router._validate_position` rejects non-finite and out-of-range lon/lat on both `GeoJSONPoint` and `GeoJSONPolygon`; `test_point_with_invalid_coordinates_is_422`, `test_polygon_with_out_of_range_coordinates_is_422`. Self-intersection (`ST_IsValid`) is **deferred**: it needs a DB round trip from a pydantic field validator, not a one-liner at this layer.
  - SUGGESTION: API tests miss 403 for producer/viewer writes, cross-org `PATCH /plots` 404, technician write, cursor paging and null-body cases. **Resolved in T2b**: `test_non_writer_roles_cannot_patch_a_farm`, `test_non_writer_roles_cannot_create_a_plot`, `test_non_writer_roles_cannot_patch_a_plot`, `test_patching_a_plot_of_a_foreign_org_is_404`, `test_technician_can_write_a_plot`, `test_get_farms_pages_by_cursor`, plus the null-body tests above.
  - Found during T2b (not a pre-existing follow-up): the non-finite-coordinate validator turned into an unhandled `500` because FastAPI's default `RequestValidationError` handler echoes the raw invalid `input` back into the response, and Starlette's `JSONResponse` refuses to serialize `NaN`/`Infinity` (`allow_nan=False`). Fixed with a shared `RequestValidationError` handler in `shared/errors.py` that sanitizes non-finite floats before serializing.
  - Tracked separately in GitHub issue #21 (parent-managed, not closed here): `tests/farms/test_api.py`'s `_member` fixture built its unique `phone` from `uuid7().int % 100000`, a collision risk on the unique column. Fixed in this commit by switching to an `itertools.count()` sequence.
- Reviewed boundary is now `17c3943`.
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

- 2026-09-23: T2 done by a delegated `sonnet-high` writer. `farms` module additions: `domain/models.py`
  (`WRITE_ROLES`, `ensure_can_write`), `domain/errors.py` (`FarmNotFoundError`, `PlotNotFoundError`,
  `InsufficientRoleError`), `adapters/geojson.py` (WKT <-> GeoJSON conversion, no shapely
  dependency), `adapters/repositories.py` (`create`/`update`/`get_for_orgs` on both repositories,
  cursor-paginated `list_for_org`, deterministic `ORDER BY id` on both list methods),
  `application/ports.py` (`FarmRepository`/`PlotRepository` protocols — needed for the hexagonal
  layering rule itself, not for a test double: writes still hit real Postgres per
  `server/tests/conftest.py`), `application/manage_farms.py` (`resolve_farm_access`,
  `create_farm`, `update_farm`), `application/manage_plots.py` (`resolve_plot_access`,
  `create_plot`, `update_plot`), `adapters/api/router.py` + `deps.py` (six endpoints registered
  in `main.py`). Migration `6628f7c0aa3b` (chained off `9098dc0927a3`, T1 review follow-ups):
  `uq_farm_id_org_id`, composite FK `fk_plot_farm_id_org_id`, `ck_plot_irrigation_efficiency_range`,
  `ck_plot_system_flow_positive`, `ix_plot_org_farm`.
  - Decisions:
    - Write roles: docs/04-api.md is silent on which membership roles may write farms/plots.
      Applied owner and technician write, producer and viewer read only (`ensure_can_write`),
      mapped to `403` — a same-org member who can't write is a different case from cross-org
      access, which stays `404` per docs/09.
    - Page<Farm>: docs/04-api.md's endpoint signature doesn't list query params for `GET
      /farms`, but its general pagination convention (`?limit=&cursor=` → `{items, next_cursor}`)
      applies generically. Implemented cursor pagination ordered by `id` (uuid7 is time-ordered):
      `limit` (default 50, max 200) and an opaque `cursor` that is the last item's `id`.
    - A farm-id-only or plot-id-only route (no `org_id` in the path) can't call
      `resolve_org_membership` with a known org. `resolve_farm_access`/`resolve_plot_access`
      fetch the caller's memberships first, then query the repository with
      `org_id IN (<the caller's org ids>)` (`get_for_orgs`) — still an org_id-filtered query
      (docs/09), never an unscoped lookup by id alone.
    - PATCH is a true partial update: `payload.model_dump(exclude_unset=True)` distinguishes an
      omitted field (unchanged) from an explicit `null` (cleared), then `dataclasses.replace`
      merges onto the current entity before re-validating the rainfed rule on the *merged*
      state — so switching `irrigation_system` to `none` while leaving old efficiency/flow
      unset is rejected as `422`, not left inconsistent or leaked as a `500` from the DB `CHECK`.
    - `Idempotency-Key` (docs/04 conventions): deliberately deferred, not implemented. Storing
      and replaying keyed responses per org isn't a small addition, and no task in this epic
      depends on it yet; noted in the router module docstring.
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/).
    RED observed via collection errors before implementation: `uv run pytest -q tests/farms` →
    `ImportError: cannot import name 'InsufficientRoleError' from 'techcamp.farms.domain.errors'`
    and `ModuleNotFoundError: No module named 'techcamp.farms.adapters.geojson'` (2 errors during
    collection, interrupted). Implemented across domain/adapters/application/API layers: GREEN,
    `uv run pytest -q tests/farms` → `48 passed`. REFACTOR: switched FastAPI query params from
    `Query(...)` defaults to `Annotated[..., Query()]` to satisfy ruff B008; widened `changes`
    dict type to `dict[str, Any]` to satisfy mypy strict against `dataclasses.replace`.
  - Verification (server/): `uv run pytest -q` → `77 passed, 2 warnings`; `uv run ruff check .` →
    `All checks passed!`; `uv run ruff format --check .` → `97 files already formatted`; `uv run
    mypy` → `Success: no issues found in 76 source files`; `uv run lint-imports` → `Hexagonal
    layers per module KEPT, 1 kept, 0 broken`; `uv run alembic upgrade head` → applied
    `6628f7c0aa3b`; `uv run alembic downgrade -1` → reverted it; `uv run alembic upgrade head` →
    reapplied clean.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start.
  - Commit: `f823f1e` — `feat(farms): add farm and plot endpoints with org isolation`. Authored
    lines (`git diff --stat` for this commit's files, excluding `server/uv.lock`): 1509
    insertions, 10 deletions across 17 files (7 modified, 10 new). This is well over the ~350
    forecast and the ~400-line delivery heuristic in a single commit — flagging for the owner/
    parent orchestrator's delivery-strategy decision (stacked-to-main slicing) before merge;
    not re-split here since the task specified one work-unit commit for T2.
  - Doc gap carried from T1, unchanged: `farm.municipality_code` is plain `text`, not yet a real
    FK (docs/03-modelo-datos.md:89 models a `municipality` table that isn't migrated from v1).

- 2026-09-23: T2b done by a delegated `sonnet-high` writer. Resolves all five T2 review
  follow-ups (see Review section above for the per-finding resolution and test names). No
  migration: only application/adapter code and tests changed.
  - `farms/domain/errors.py`: `InvalidTechnicianError`.
  - `farms/application/manage_farms.py`: `_ensure_valid_technician` (owner/technician
    membership check via `MembershipRepository.get`), called from `create_farm` and
    `update_farm` before the write.
  - `farms/application/manage_plots.py`: `update_plot` applies `default_efficiency_for` when
    the merged state switches to an irrigated system with no efficiency set.
  - `farms/adapters/api/router.py`: `_reject_explicit_null` (shared by `patch_farm`/
    `patch_plot`), `_validate_position` + `field_validator`s on `GeoJSONPoint`/`GeoJSONPolygon`,
    `InvalidTechnicianError` → `422` in `post_farm`/`patch_farm`.
  - `shared/errors.py`: a `RequestValidationError` handler that sanitizes non-finite floats
    before `JSONResponse` serializes them (see Review: found during this task, not a
    pre-existing follow-up).
  - `tests/farms/test_api.py`: 24 new tests (see Review); `_member`'s phone fixture switched
    from `uuid7().int % 100000` to an `itertools.count()` sequence (GitHub issue #21,
    parent-managed — this commit fixes it but does not close the issue).
  - Decisions:
    - `technician_id` valid role: docs/03 says `technician_id FK "técnico asignado"` but is
      silent on which membership role qualifies. Reused `WRITE_ROLES` (owner or technician) —
      the same set already used for who may write farms/plots, per the task's fallback
      instruction.
    - Self-intersection (`ST_IsValid`) validation stays deferred: rejecting it needs a DB round
      trip from inside a pydantic `field_validator` (no DB session available there), so it's not
      the one-liner the task allowed; would need a new port/adapter call before insert.
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/).
    RED observed by running the 27 new/changed tests against pre-fix code: `uv run pytest -q
    tests/farms/test_api.py` → 12 failed (2 client-side `ValueError: Out of range float values
    are not JSON compliant` from httpx itself, adjusted to send raw `content=` for those two
    cases; the other 10 are the real server-side bugs: `IntegrityError` 500s on explicit null,
    `TypeError`/`AttributeError` on null `boundary`/`irrigation_system`, `201` where `422` was
    expected for foreign/wrong-role `technician_id` and out-of-range/non-finite coordinates,
    `None` instead of the default efficiency on system switch). Implemented the fixes: GREEN,
    `uv run pytest -q tests/farms/` → `71 passed`. One further RED found mid-implementation: the
    non-finite-coordinate case surfaced a framework-level `500` (Starlette `JSONResponse`
    `allow_nan=False` crashing on the echoed invalid `input`), fixed with the shared
    `RequestValidationError` handler. REFACTOR: `ruff format` wrapped two lines past 100 cols
    (`_reject_explicit_null`'s comprehension, `update_plot`'s switch condition).
  - Verification (server/): `uv run pytest -q` → `100 passed, 2 warnings`; `uv run ruff check .`
    → `All checks passed!`; `uv run ruff format --check .` → all files formatted (2 reformatted
    on the first pass); `uv run mypy` → `Success: no issues found in 76 source files`; `uv run
    lint-imports` → `Hexagonal layers per module KEPT, 1 kept, 0 broken`.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start.
  - Commit: `86909fa` — `fix(farms): reject null patches and foreign technicians with 422`.
    Authored lines (`git diff --stat` for this commit's files, excluding `server/uv.lock`): 445
    insertions, 4 deletions across 6 files. Above the ~150 forecast and at the ~400-line delivery
    heuristic in a single commit, for the same reason as T2: one work-unit commit was specified
    for this task, and the added API tests (333 lines) are most of the diff. Flagging for the
    owner/parent orchestrator's delivery-strategy decision, not re-split here.
  - Doc gap carried from T1/T2, unchanged: `farm.municipality_code` is plain `text`, not yet a
    real FK.

## Next step
T3 crop catalog (`crop` + `crop_stage` migration, FAO-56 Table 12 seed, `GET /crops`).
