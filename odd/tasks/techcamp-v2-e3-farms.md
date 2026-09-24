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
- [x] T3 Crop catalog: `crop` + `crop_stage` migration, seed with FAO-56 Table 12 Kc and `kc_source`, `GET /crops` — route: delegated — forecast ~300 — actual 428
- [x] T3b Fix #21 round 4: switching to rainfed clears efficiency and flow (ADR-0023) with a test; cassava stage split + seed test that stage lengths follow the rule and sum to the cycle; assert setup 201 — route: delegated — forecast ~80 — actual ~160
- [x] T4 Soil profile: `soil_profile` migration, `PUT /plots/{id}/soil`, FAO-56 Table 19 texture fallback — route: delegated — forecast ~250 — actual 590
- [x] T5 Soil autofill: SoilGrids port + adapter + test double, `POST /plots/{id}/soil:autofill` (seminar: recorded fixture; ADR-0021 row) — route: delegated — forecast ~250 — actual 733
- [ ] T5b Fix #21 round 7: every malformed SoilGrids 200 body → 502 problem+json; test that the centroid reaches SoilGrids as correct lon/lat and query params — route: delegated — forecast ~100
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
- T2b `044fce2..e05bce8` (medium, fix + `AGENTS.md`, 519 lines; owner granted): lineage `review-2652a2e02235f811` approved and acknowledged. Findings tracked in #21:
  - WARNING: `update_farm` re-validated a stale `technician_id` on every PATCH, blocking unrelated edits. **Resolved** in the next commit (parent, inline TDD): validate only when the PATCH sets `technician_id`; RED `assert 422 == 200` in `test_stale_technician_does_not_block_an_unrelated_patch`, GREEN `101 passed`, ruff/format/mypy/lint-imports green.
  - SUGGESTION (open in #21): explicit `irrigation_efficiency: null` on an irrigated plot silently becomes the default; the phone counter depends on per-test cleanup of committed users.
- Review findings are tracked in GitHub issue #21 (rule added to `AGENTS.md` in `1152a61`).
- Whole branch `4684262..a23d6cf` (stop-hook, standing grant): lineage `review-8c6a346f3ac1752e`, approved and acknowledged. WARNING: switching between irrigated systems kept the previous efficiency; **resolved** in `799f6d8` (parent, inline TDD: RED `comparison failed` in `test_switching_between_irrigated_systems_uses_the_new_default`, GREEN `102 passed`, all checks green). Two suggestions open in #21 (validation 422 not problem+json; multi-ring polygon round trip untested).
- T3 slice `e05bce8..a337f57` (T3 + parent fixes `1f4efde`, `799f6d8`; medium, new migration, 589 lines, `slice_budget_reached`, standing grant): lineage `review-576ae0774ec7ef76`, approved and acknowledged. WARNING: switching to rainfed clears efficiency but keeps flow (untested path); suggestions: cassava stage split, missing 201 assert. All tracked in #21 round 4; fixed by task T3b. Reviewed boundary is now `a337f57`.
- T3b `a337f57..81f57e3` (fix, no migration, 160 lines): resolves #21 round 4
  — the T3 slice's WARNING and both suggestions above, plus two items the
  parent orchestrator added mid-task (see T3b progress below for all five).
  #21 round 4 resolved in `81f57e3`. RDD assessment/acknowledgement for this
  commit not run by this writer — left to the parent orchestrator; boundary
  not advanced here.
- T4 `81f57e3..3703cbe` (new migration, 590 lines, likely `slice_budget_reached`): RDD
  assessment/acknowledgement not run by this writer — left to the parent orchestrator; boundary
  not advanced here.
- T3b + T4 slice `a337f57..111cffc` (medium, 907 lines, standing grant): lineage `review-7d6076ce2e31e46b`, approved and acknowledged. WARNING (cassava seed edited in place in `67cf2dd1f13e`) accepted: revision only on this unmerged branch. Three test-strength suggestions open in #21 round 6. Reviewed boundary is now `111cffc`.
- T5 slice `111cffc..47f0b83` (T5 + parent USDA texture fix; medium, 851 lines, standing grant): lineage `review-23b5b52e438ac3e2`, approved and acknowledged. Two WARNINGs (nested malformed SoilGrids body → 500; centroid lon/lat query unproved) tracked in #21 round 7, fixed by task T5b. Reviewed boundary is now `47f0b83`.
- Local only: `.impeccable/surfaces/config.local.json` is listed in `.git/info/exclude` so RDD candidate selection ignores it.

## Acceptance criteria
- [ ] A user creates a farm and a plot from a drawn polygon; `area_ha` comes from the geometry.
- [ ] Rainfed plots reject irrigation efficiency and flow (DB `CHECK` and 422).
- [x] Soil autofill fills θFC/θWP from SoilGrids, or from the FAO-56 texture table with `source = fao56_texture`.
- [x] `GET /crops` returns stages, Kc and `kc_source`.
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

- 2026-09-23: T3 done by a delegated `sonnet-high` writer. `farms` module additions:
  `domain/models.py` (`CROP_STAGES`, `KcSource`, `CropStage`, `Crop` dataclasses),
  `adapters/orm.py` (`CropRow`, `CropStageRow` — global reference data, no `org_id`),
  `adapters/repositories.py` (`SqlAlchemyCropRepository.list_all`, stages sorted to
  `CROP_STAGES` order), `adapters/api/deps.py` (`CropRepoDep`), `adapters/api/router.py`
  (`GET /crops` → `CropView[]`, `CurrentUserId`-only, no application-layer function — same
  pattern as `GET /farms`'s direct repository call, no business rule to enforce for a
  read-only catalog). Migration `67cf2dd1f13e` (chained off `6628f7c0aa3b`): `crop` and
  `crop_stage` tables plus the 12-crop seed from v1's `crops_requirements.csv`
  (docs/03-modelo-datos.md:489), inline in the migration (ponytail: shortest reviewable
  option — no CSV parsing at migration time).
  - Confirmed per task: `crop`/`crop_stage` have no `org_id` column in docs/03-modelo-datos.md:115-127
    — global reference data, not org-scoped, matching the ponytail note in this task's brief.
  - Decisions (FAO-56 = Allen, Pereira, Raes & Smith, 1998, Irrigation and Drainage Paper 56,
    fao.org; Table 12 = Kc initial/mid/end per crop category, Table 22 = `p`, the no-stress
    depletion fraction at ETc=5mm/day):
    - `kc_source = fao56` (7 crops, direct Table 12 category): maize (Cereals: Maize, grain),
      rice (Rice, wetland, continued flooding), beans (Legumes: Beans, dry), cacao (Tropical
      Fruits and Trees: Cacao), cotton (Fiber Crops: Cotton, leaves not removed), sorghum
      (Cereals: Sorghum, grain), chili_pepper/Ají (Vegetables – Solanum Family: Peppers, bell).
    - `kc_source = approximate` (4 crops, mapped to the closest Table 12 entry, a different
      species/variant): cassava/Yuca → Cassava, year 1 (v1 doesn't record plant age to choose
      year 1 vs. year 2 canopy); plantain/Plátano → Banana, 1st year (same genus Musa, not
      tabulated separately); oil_palm/Palma Aceitera → Palm Trees (generic evergreen palm
      canopy, oil palm isn't tabulated separately); mango → Avocado (closest evergreen
      sub/tropical fruit-tree analog, mango isn't tabulated).
    - `kc_source = none` (1 crop): yam/Ñame — not in FAO-56 Table 12, docs/03-modelo-datos.md:446's
      own example; no `crop_stage` rows.
    - `development`-stage Kc is not a Table 12 value (FAO-56 ramps it linearly from Kc initial
      to Kc mid); used the ramp's midpoint, `(kc_initial + kc_mid) / 2`, hardcoded per crop in
      the migration (not computed at runtime, to avoid floating-point rounding surprises in a
      data migration).
    - `depletion_fraction_p` (Table 22) is one value per crop, not per stage (FAO-56 doesn't
      vary `p` by growth stage); the same value is stored on all four `crop_stage` rows.
    - `length_days` is **not** a literal FAO-56 Table 11 row (Table 11's rows are
      region/planting-date examples that don't match this Caribbean dataset's varieties or v1's
      total cycle days); instead each crop's four stage lengths are a 20/30/35/15%
      initial/development/mid/late split of its `ciclo_dias` from `crops_requirements.csv`,
      `mid` absorbing the rounding remainder so the four stages sum exactly to that documented
      total cycle. Flagged as a modeling simplification, not verified against a specific table
      row, per the "never invent values" instruction — this affects only the descriptive stage
      length, not the Kc/`p` values the irrigation math (E6) actually consumes.
    - `crop.id` is a plain integer PK with explicit values 1–12 assigned in the seed (no
      sequence): the catalog has no `POST /crops` (out of scope, admin/migration-managed data),
      so there's nothing to autoincrement for.
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/).
    RED observed two ways: (1) `git stash` of the implementation files (keeping the new
    migration and tests) → `uv run pytest -q tests/farms/test_crop_seed.py` → 1 collection
    error, `ImportError: cannot import name 'SqlAlchemyCropRepository'`; (2)
    `tests/farms/test_api.py::test_get_crops_*` against the same pre-fix code → 2 failed
    (`404` instead of `200`/`401`, the `/crops` route didn't exist yet). Restored the
    implementation: GREEN, `uv run pytest -q tests/farms/test_crop_seed.py tests/farms/test_api.py`
    → `42 passed`. REFACTOR: none needed beyond the formatting already applied while writing.
  - Verification (server/): `uv run pytest -q` → `107 passed, 2 warnings`; `uv run ruff check .`
    → `All checks passed!`; `uv run ruff format --check .` → `98 files already formatted`;
    `uv run mypy` → `Success: no issues found in 76 source files`; `uv run lint-imports` →
    `Hexagonal layers per module KEPT, 1 kept, 0 broken`; `uv run alembic upgrade head` →
    applied `67cf2dd1f13e`; `uv run alembic downgrade -1` → reverted it; `uv run alembic
    upgrade head` → reapplied clean.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start.
  - Commit: `a1ad865` — `feat(farms): add crop catalog with FAO-56 Kc stages`. Authored lines
    (`git diff --stat --cached` for this commit's files, excluding `server/uv.lock`): 433
    insertions, 5 deletions across 8 files (2 new, 6 modified) — within the ~400-line delivery
    heuristic.
  - Doc gap carried from T1/T2, unchanged: `farm.municipality_code` is plain `text`, not yet a
    real FK.

- 2026-09-23: T3b done by a delegated `sonnet-high` writer. Resolves GitHub issue #21 round 4
  (T3 review WARNING and two suggestions) plus two items the parent orchestrator added mid-task.
  - `farms/application/manage_plots.py` `update_plot`:
    - Switching to `irrigation_system: none` without sending `system_flow_lph` now clears it
      too (mirrors the existing efficiency-clearing via `default_efficiency_for(NONE)`), instead
      of leaking the old system's flow into `ensure_rainfed_has_no_irrigation` and wrongly
      raising `422`. New `system_changed_to_rainfed` guard, flow cleared only when the field is
      omitted from the request.
    - An explicit JSON `null` on `irrigation_efficiency` while the resulting plot stays or
      becomes irrigated is now a `422` (`MissingIrrigationEfficiencyError`, new domain error in
      `farms/domain/errors.py`), instead of silently applying the system default. The default
      still applies when the field is omitted (unchanged `system_changed_without_efficiency` /
      `missing_efficiency` branches, now reached only after the explicit-null guard).
  - `farms/adapters/api/router.py` `patch_plot`: maps `MissingIrrigationEfficiencyError` → `422`
    problem+json ("Irrigated plot requires an efficiency"), same pattern as
    `RainfedPlotHasIrrigationError`.
  - `migrations/versions/67cf2dd1f13e_add_crop_catalog.py`: cassava's stage-length tuple was
    `(54, 80, 95, 41)`, not the documented 20/30/35/15 split of its 270-day cycle (`0.30 * 270 =
    81` exactly, not 80); fixed to `(54, 81, 94, 41)` — `initial`/`development`/`late` at
    round-half-up 20/30/15%, `mid` absorbing the remainder, sum unchanged at 270.
  - `tests/farms/test_api.py`: renamed/fixed `test_patching_a_plot_to_rainfed_with_leftover_flow_is_422`
    to `test_patching_a_plot_to_rainfed_clears_leftover_efficiency_and_flow` (now asserts `200`
    and both fields `null`, since the old `422` was the bug); added
    `test_patching_a_plot_to_rainfed_with_explicit_flow_is_422` (explicit non-null `system_flow_lph`
    with `none` still `422`); added `test_explicit_null_efficiency_on_a_plot_that_stays_irrigated_is_422`;
    added `test_patching_a_plots_boundary_recomputes_area_ha` (patches `boundary` to a larger
    polygon, asserts `area_ha` changes — proves the DB-generated column recomputes on `UPDATE`,
    not only on `INSERT`); added `assert created.status_code == 201, created.text` to
    `test_stale_technician_does_not_block_an_unrelated_patch`'s setup POST.
  - `tests/farms/test_crop_seed.py`: `test_stage_lengths_follow_the_20_30_35_15_split` asserts,
    for every seeded crop with stages, that `initial`/`development`/`late` are round-half-up
    20/30/15% of the summed cycle (`sum` of the four stored stage lengths — `length_days` isn't
    stored elsewhere) and `mid` is the exact remainder.
  - Decisions:
    - Scope added mid-task by the parent orchestrator, both folded into this commit (still
      `Refs #21`, no new task): (a) `test_patching_a_plots_boundary_recomputes_area_ha` above;
      (b) explicit-null-efficiency now `422` instead of silently defaulted — see
      `MissingIrrigationEfficiencyError` above. This decision applies to `update_plot` (`PATCH`)
      only: `create_plot` (`POST`) can't distinguish an omitted `irrigation_efficiency` from an
      explicit `null` (both arrive as `None` on a Pydantic field with `default=None`, no
      `exclude_unset` payload there), so its existing default-on-`None` behavior is unchanged.
    - `MissingIrrigationEfficiencyError.irrigation_system` is typed `str`, not `IrrigationSystem`:
      `domain/errors.py` must not import `domain/models.py` (`models.py` already imports
      `errors.py`; importing back would cycle).
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/).
    RED observed by `git stash push` of the four implementation files only (migration, router,
    manage_plots, errors), keeping the new/changed tests: `uv run pytest -q
    tests/farms/test_api.py -k "rainfed_clears or explicit_flow_is_422 or explicit_null_efficiency
    or recomputes_area_ha or stale_technician" tests/farms/test_crop_seed.py::test_stage_lengths_follow_the_20_30_35_15_split`
    → 2 failed (`422` where `200` was expected for the rainfed-clears case; `200` where `422` was
    expected for explicit-null-efficiency), the other three already passing (unrelated to this
    bug or the `-k` filter dropped the crop-seed test from that combined run); the crop-seed
    split test run alone → 1 failed, `cassava: assert 80 == 81`. `git stash pop` restored the
    implementation: GREEN, `uv run pytest -q` → `111 passed`. REFACTOR: none needed.
  - Verification (server/): `uv run pytest -q` → `111 passed, 2 warnings`; `uv run ruff check .`
    → `All checks passed!`; `uv run ruff format --check .` → `98 files already formatted`;
    `uv run mypy` → `Success: no issues found in 76 source files`; `uv run lint-imports` →
    `Hexagonal layers per module KEPT, 1 kept, 0 broken`.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start.
  - Commit: `81f57e3` — `fix(farms): clear irrigation fields on switch to rainfed and fix cassava
    stages`. Authored lines (`git diff --stat` for this commit's files, excluding
    `server/uv.lock`): 160 insertions, 3 deletions across 6 files (0 new, 6 modified). Above the
    ~80 forecast, expected: the parent orchestrator added two more items mid-task after the
    forecast was set.
  - Doc gap carried from T1/T2/T3, unchanged: `farm.municipality_code` is plain `text`, not yet a
    real FK.

- 2026-09-23: T4 done by a delegated `sonnet-high` writer. `farms` module additions:
  `domain/models.py` (`SoilProfileSource`, `SoilProfile`, `FAO56_TEXTURE_WATER_LIMITS`,
  `apply_fao56_texture_fallback` — pure), `adapters/orm.py` (`SoilProfileRow`, no `org_id`:
  access gated through the plot), `adapters/repositories.py` (`SqlAlchemySoilProfileRepository.put`,
  Postgres `INSERT ... ON CONFLICT (plot_id) DO UPDATE`), `application/ports.py`
  (`SoilProfileRepository`), `application/manage_soil.py` (`put_soil_profile`, reuses
  `manage_plots.resolve_plot_access` + `ensure_can_write`), `adapters/api/deps.py`
  (`SoilProfileRepoDep`), `adapters/api/router.py` (`PUT /plots/{plot_id}/soil` →
  `SoilProfileView`). Migration `7f9c1b3cae7f` (chained off `67cf2dd1f13e`): `soil_profile`
  table (`plot_id` PK/FK to `plot.id`, range `CHECK`s, `θWP < θFC` `CHECK`, `source` `CHECK`
  restricted to `soilgrids|lab|fao56_texture`).
  - Decisions:
    - FAO-56 Table 19 texture fallback: the writer found only Example 36's three values and
      scoped the fallback to three classes. **Superseded by the parent in `57ae49a`**: Table 19 is
      published in full at fao.org/4/x0490e/x0490e0c.htm (verified 2026-09-23). docs/03:445 asks
      for the class mean, so all nine USDA classes use the midpoint of each θFC/θWP range (e.g.
      silt 32/17 %, not Example 36's 32/15). TDD: RED 11 failed, GREEN `133 passed`; ruff, format,
      mypy, lint-imports green.
    - `soil_profile` has no `org_id` column, matching docs/03-modelo-datos.md:106's field list
      exactly (only `plot_id PK, FK`): access is gated once through
      `manage_plots.resolve_plot_access` (org-scoped) before the soil repository is ever
      touched, the same defense-in-depth boundary the read-only `crop`/`crop_stage` tables use
      for a different reason (global data, T3).
    - `source` is nullable (docs/03 doesn't mark it `NOT NULL`): set to `lab` when the client
      sends both θFC and θWP, `fao56_texture` when the pure fallback fills them from a verified
      texture class, and left `null` when neither is available (no lab values, no recognized
      texture) — an honest "not yet determined" state, not an invented third source.
    - `PUT` is a full-document upsert (docs/04-api.md:49: `SoilProfile → SoilProfile`, no
      separate create endpoint), not a `PATCH`-style partial merge: a second `PUT` replaces every
      field, including clearing ones the first call set and the second omits (test
      `test_putting_a_soil_profile_twice_replaces_it`).
    - Router-level validation (pydantic, same layer as the existing GeoJSON/explicit-null
      checks): `ph` in [0, 14], `organic_matter_pct`/`field_capacity_pct` in (0, 100],
      `wilting_point_pct` in [0, 100), `root_depth_cm` > 0, θFC and θWP must be given together
      (not just one), and θWP must be strictly less than θFC when both are given — all `422`.
    - No `GET /plots/{plot_id}/soil`: docs/04-api.md:43-56 lists only `PUT` and
      `POST .../soil:autofill` (T5) for this resource, per the task's explicit instruction.
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/).
    RED observed by moving the two new files aside (migration, `manage_soil.py`) and
    `git stash`-ing the six modified implementation files, keeping the new/changed tests:
    `uv run pytest -q tests/farms/test_domain_models.py` → 1 collection error,
    `ImportError: cannot import name 'SoilProfileSource'`; `uv run pytest -q
    tests/farms/test_api.py -k soil` → 8 failed (404 "Not Found" — the route didn't exist —
    where 200/403/422 were expected, and 404 with the wrong `content-type` where problem+json
    was expected). Restored the implementation: GREEN, `uv run pytest -q` → `128 passed`.
    REFACTOR: `ruff format` wrapped three long `client.put(...)` calls in the new API tests.
  - Verification (server/): `uv run pytest -q` → `128 passed, 2 warnings`; `uv run ruff check .`
    → `All checks passed!`; `uv run ruff format --check .` → `99 files already formatted` (1
    file reformatted first pass); `uv run mypy` → `Success: no issues found in 77 source files`;
    `uv run lint-imports` → `Hexagonal layers per module KEPT, 1 kept, 0 broken`; `uv run alembic
    upgrade head` → applied `7f9c1b3cae7f`; `uv run alembic downgrade -1` → reverted it; `uv run
    alembic upgrade head` → reapplied clean.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start.
  - Commit: `3703cbe` — `feat(farms): add soil profile with FAO-56 texture fallback`. Authored
    lines (`git diff --stat --cached` for this commit's files, excluding `server/uv.lock`): 590
    insertions, 5 deletions across 10 files (2 new, 8 modified). Above the ~250 forecast, for the
    same reason as T2/T2b/T3b: one work-unit commit was specified for this task, and the full
    CRUD stack (domain + orm + repository + port + application + deps + router + migration) plus
    two test files (~20 new tests) don't split smaller within a single task. Flagging for the
    owner/parent orchestrator's delivery-strategy decision, not re-split here.
  - Doc gap carried from T1/T2/T3: `farm.municipality_code` is plain `text`, not yet a real FK.

- 2026-09-23: T5 done by a delegated `sonnet-high` writer. `farms` module additions:
  `domain/models.py` (`SoilGridsSample`, `ORGANIC_CARBON_TO_MATTER_FACTOR`, `classify_usda_texture`,
  `build_soil_profile_from_soilgrids` — pure), `domain/errors.py` (`SoilGridsUnavailableError`),
  `application/ports.py` (`SoilGridsPort`; `PlotRepository.get_centroid`), `adapters/repositories.py`
  (`SqlAlchemyPlotRepository.get_centroid`, `ST_X(ST_Centroid(boundary))`/`ST_Y(...)`),
  `adapters/soilgrids.py` (new: `IsricSoilGridsAdapter`, `seminar_soilgrids_adapter`,
  `SEMINAR_FIXTURE_RESPONSE`), `application/manage_soil.py` (`autofill_soil_profile`),
  `adapters/api/deps.py` (`get_soilgrids_port`, `SoilGridsPortDep`), `adapters/api/router.py`
  (`POST /plots/{plot_id}/soil:autofill`). No migration: `soil_profile.source` already allows
  `soilgrids` (T4).
  - Decisions:
    - One `IsricSoilGridsAdapter` class serves both real implementations the port needs
      (ADR-0002: a port only for external I/O needing a test double): a live network call for
      production, and an injected `httpx.MockTransport` for the seminar profile's recorded
      fixture and for this task's own adapter tests (ponytail: reuse over a second class, since
      httpx already provides exactly the transport-injection mechanism needed).
    - SoilGrids API verified live (2026-09-23, WebFetch against docs.isric.org/rest.isric.org,
      cross-checked against the `soilDB` R package's response-parsing source on GitHub):
      `/properties/query` (`lon`, `lat`, repeated `property`/`depth`, `value=mean`); six standard
      depths (`0-5cm` … `100-200cm`), this adapter reads only `0-5cm` (topsoil; a documented
      simplification, `ponytail:` comment on `IsricSoilGridsAdapter`'s docstring — depth-weighting
      the root zone is out of scope); each `properties.layers[].unit_measure.d_factor` converts
      the mapped integer to the documented conventional unit, read from the response itself
      rather than hardcoded.
    - The live `/properties/query` endpoint returned `503 Service Unavailable` on every fetch
      attempt during this task (2026-09-23): the seminar/test fixture (`SEMINAR_FIXTURE_RESPONSE`)
      is built from the documented v2.0 schema, not a captured live response — disclosed in the
      adapter module's docstring and in `soilgrids.py`'s fixture comment, per this task's
      instruction to say so explicitly when that happens.
    - Organic matter: `soc` (organic carbon) → organic matter % uses the conventional van
      Bemmelen factor 1.724 (verified via WebSearch: Minasny et al., 2020, "Precocious 19th
      century soil carbon science", Geoderma Regional — attributed to van Bemmelen, 1890, still
      the conventional default despite known soil-to-soil error). Cited, not invented, per this
      task's instruction.
    - USDA texture triangle: the writer shipped axis-aligned bands and claimed they only affected
      the `texture` string. **Superseded by the parent in `816ef5b`**: the bands misclassified
      sand, sandy clay loam and sandy clay, and a wrong class also changes θFC/θWP whenever
      SoilGrids lacks `wv0033`/`wv1500`, because the fallback is keyed by texture. Now uses the
      NRCS class rules (Soil Survey Manual ch. 3), cross-checked against the `USDA.TT` vertex
      table of the `soiltexture` CRAN package (NRCS page unreachable). TDD: RED 4 failed, GREEN
      `169 passed`; ruff, format, mypy, lint-imports green.
    - θFC/θWP: `source = soilgrids` only when SoilGrids returns **both** `wv0033` and `wv1500` at
      the query point; a lone one of the two falls back to the FAO-56 texture means rather than
      mixing a real SoilGrids value with an invented pair.
    - `root_depth_cm` is never set by autofill (task instruction: "stays as docs say") — SoilGrids
      has no such property, and root depth is a crop/rooting choice, not a soil property. Since
      `soil_profiles.put` is the same full-document-replace upsert T4 established, a later
      autofill call clears any manually-set `root_depth_cm`, same replace semantics as `PUT`.
    - 502 vs 503 (docs/04-api.md is silent on which): 503 when the request never reached ISRIC
      (timeout, connection failure — `upstream_status=None`), 502 when ISRIC answered with a
      non-200 status — a standard REST convention (our service vs. a bad upstream response),
      documented on `SoilGridsUnavailableError`.
    - Response status: `POST .../soil:autofill` returns `200` (no `status_code` override), same
      as `PUT /plots/{id}/soil` — it upserts the same resource, not a new one.
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/).
    RED observed by moving the new adapter file aside and `git stash`-ing the seven modified
    implementation files, keeping the new/changed tests: `uv run pytest -q tests/farms/test_domain_models.py
    tests/farms/test_soilgrids_adapter.py tests/farms/test_api.py -k "soilgrids or autofill or
    usda_texture or classify"` → 3 collection errors (`ImportError: cannot import name
    'SoilGridsSample'`; `ModuleNotFoundError: No module named 'techcamp.farms.adapters.soilgrids'`;
    `ImportError: cannot import name 'get_soilgrids_port'`). Restored the implementation: GREEN,
    same selection → `98 passed`; full `uv run pytest -q` → `154 passed`. REFACTOR: none needed
    beyond the mypy/ruff fixes below.
  - Verification (server/): `uv run pytest -q` → `154 passed, 2 warnings`; `uv run ruff check .`
    → `All checks passed!` (two `E501` long-line fixes: `router.py`'s exception mapping,
    `soilgrids.py`'s `_extract_conventional` signature); `uv run ruff format --check .` → `101
    files already formatted`; `uv run mypy` → `Success: no issues found in 78 source files` (two
    fixes: an explicit `float | None` annotation for `field_capacity_pct`/`wilting_point_pct` in
    `build_soil_profile_from_soilgrids`, and `httpx.AsyncBaseTransport` instead of `BaseTransport`
    on `IsricSoilGridsAdapter`'s `transport` parameter — `MockTransport` implements both, but
    `AsyncClient` only accepts the async one); `uv run lint-imports` → `Hexagonal layers per
    module KEPT, 1 kept, 0 broken`.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start; no migration in
    this task, so no `alembic upgrade`/`downgrade` cycle to verify.
  - Commit: `ee3e4f4` — `feat(farms): autofill soil profile from SoilGrids with seminar fixture`.
    Authored lines (`git diff --stat --cached -- . ':!server/uv.lock'`): 728 insertions, 5
    deletions across 12 files (2 new, 10 modified) — well above the ~250 forecast and the
    ~400-line delivery heuristic in a single commit, same reason as T2/T2b/T3b/T4: one work-unit
    commit was specified for this task, and the full port+adapter+application+router stack plus
    three test files (~30 new tests) don't split smaller within a single task. Flagging for the
    owner/parent orchestrator's delivery-strategy decision, not re-split here.
  - Doc gaps: (1) carried from T1-T4, unchanged — `farm.municipality_code` is plain `text`, not
    yet a real FK. The USDA texture gap was
    resolved in `816ef5b` (see Decisions above).

## Next step
T6 crop cycles (`crop_cycle` migration, one active cycle per plot,
`POST /plots/{id}/cycles`, `PATCH /cycles/{id}`).
