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

- Web (owner, 2026-09-24): the E1 design is kept as-is for now ("initial bridge"): web tasks compose existing tokens, primitives and patterns only, with no visual changes; gaps are flagged, not invented.
- Web sign-in (owner, 2026-09-24): no epic owns the sign-in screen, so T7 adds a minimal phone OTP sign-in against the seminar `/dev/auth/otp` + `/dev/auth/otp/verify`, taking the org from `GET /me` (docs/07 flow: login → consent → home; consent stays out of scope).

## Tasks
- [x] T1 Farm and plot schema: `geoalchemy2` dependency, ORM rows, Alembic migration (GiST index, irrigation `CHECK`, `area_ha`), domain rules (default efficiency by system), repositories filtered by `org_id` — route: delegated — forecast ~300 — actual ~623
- [x] T2 Farm and plot endpoints: `GET/POST /farms`, `PATCH /farms/{id}`, `GET/POST /farms/{id}/plots`, `PATCH /plots/{id}`; GeoJSON Polygon validation; org isolation test; T1 review follow-ups (see Review) — route: delegated — forecast ~350 — actual ~1519
- [x] T2b Fix T2 review follow-ups: 422 on explicit nulls, validate `technician_id` membership, default efficiency on system switch, coordinate bounds, missing API tests — route: delegated — forecast ~150 — actual ~445
- [x] T3 Crop catalog: `crop` + `crop_stage` migration, seed with FAO-56 Table 12 Kc and `kc_source`, `GET /crops` — route: delegated — forecast ~300 — actual 428
- [x] T3b Fix #21 round 4: switching to rainfed clears efficiency and flow (ADR-0023) with a test; cassava stage split + seed test that stage lengths follow the rule and sum to the cycle; assert setup 201 — route: delegated — forecast ~80 — actual ~160
- [x] T4 Soil profile: `soil_profile` migration, `PUT /plots/{id}/soil`, FAO-56 Table 19 texture fallback — route: delegated — forecast ~250 — actual 590
- [x] T5 Soil autofill: SoilGrids port + adapter + test double, `POST /plots/{id}/soil:autofill` (seminar: recorded fixture; ADR-0021 row) — route: delegated — forecast ~250 — actual 733
- [x] T5b Fix #21 round 7: every malformed SoilGrids 200 body → 502 problem+json; test that the centroid reaches SoilGrids as correct lon/lat and query params — route: delegated — forecast ~100 — actual 141
- [x] T6 Crop cycles: `crop_cycle` migration (one active cycle per plot), `POST /plots/{id}/cycles`, `PATCH /cycles/{id}` — route: delegated — forecast ~250 — actual 1063
- [x] T6b Fix #21 round 8: map the partial-unique-index `IntegrityError` on cycle create to 409; reject `expected_harvest_on` < `sown_on` (422 + DB `CHECK`); assert 201 in `_create_cycle`; chain and log the original SoilGrids parse exception — route: delegated — forecast ~120 — actual 164
- [x] T7 Web data layer, minimal OTP sign-in and plots route: API client with bearer token, phone + code sign-in, farm/plot list in the plots tab (via `impeccable`, existing design only) — route: delegated — forecast ~250 — actual 988
- [x] T7b Align with docs and fix #21 round 10: server routers under `/api/v1` (docs/04:7), web client on generated OpenAPI types + `openapi-fetch` + TanStack Query (docs/05:179-180), document `POST /dev/auth/otp/verify` in docs/04; 401 → sign-out and redirect to sign-in, request timeout, atomic sign-in session, single proxy config, farms pagination, error copy by cause — route: delegated — forecast ~500 — actual 166 (server) + 667 (web)
- [x] T7c Fix #21 round 11: `VITE_API_URL` only as the dev-proxy target (non-`VITE_` variable, relative browser base, test base separate); do not sign out on 401 from anonymous OTP calls (no token on them); test the 10 s timeout and `describeApiError`; per-farm plots retry — route: delegated — forecast ~150 — actual 219
- [x] T8 Web plot creation: lazy-loaded Leaflet map, draw polygon, farm and plot forms (via `impeccable`) — route: delegated — forecast ~350 — actual 158 (map) + 701 (forms)
- [x] T8b Fix #21 round 12: `AbortSignal.any` fallback for older WebViews; real fake-timer timeout test; test list refetch after farm/plot create; `PlotDrawMap` glue test + `invalidateSize()` in the sheet + marker icon under Vite; `errorCopy` test with `ApiError`; client lat/lng range check; ADR-0021 row for map tiles (public OSM, owner 2026-09-24) — route: delegated — forecast ~150 — actual 352
- [x] T9 Web soil and cycle: soil autofill/edit and crop cycle forms with Kc shown (via `impeccable`) ; also fix #21 round 13 (timeout test cleanup in `try/finally`, fallback test proves the timeout-only path) — route: delegated — forecast ~300 — actual 40 (round 13 fix) + 1059 (T9 feature)

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
- T5b + T6 slice `47f0b83..b0c3b55` (medium, 1367 lines, standing grant): lineage `review-fb8c246391433103`, approved and acknowledged. Two WARNINGs (concurrent cycle create → 500 instead of 409; harvest date before sowing accepted) and two suggestions, tracked in #21 round 8, fixed by task T6b. Reviewed boundary is now `b0c3b55`.
- T6b slice `b0c3b55..e40baf0` (medium, 259 lines; owner granted): lineage `review-19c63dde7b4661fe`, approved and acknowledged; two suggestions in #21 round 9. Reviewed boundary is now `e40baf0` (backend complete).
- T7 `7d79b19..77bb7ad` (high: auth; 1132 lines; standing grant): lineage `review-030667a3008ca044`, 4 lenses, approved and acknowledged. Six WARNINGs (no 401 recovery, no fetch timeout, token persisted before `/me`, `VITE_API_URL` dual use, farms pagination ignored, 401 shown as connection error) plus the docs drift found by the parent (`/api/v1` prefix, docs/04:7; TanStack Query + `openapi-typescript`/`openapi-fetch`, docs/05:179-180): #21 round 10, fixed by T7b. Reviewed boundary is now `77bb7ad`.
- T7b slice `77bb7ad..0547131` (high: auth tests; 2472 lines, 27 files; owner granted): lineage `review-11b41042685dc827`, 4 lenses, approved and acknowledged (authority burned). Parent spot check: web `npm test -- --run` 47 passed, typecheck clean; server `uv run pytest -q` 212 passed. Round 10 resolved except `VITE_API_URL` dual use (still open, comment claims the opposite). Four WARNINGs (`VITE_API_URL` dual use; stale token makes an OTP 401 sign out; timeout and error copy untested; per-farm plots no retry) and five suggestions: #21 round 11, fixed by T7c. Reviewed boundary is now `0547131`.
- T7c `0547131..71445d9` (fix, medium: `infra/compose.yaml` configuration change; 324 lines): parent spot check `npm test -- --run` 56 passed; `review assess` → `review_due: false`, `under_budget`, pending in the slice until a later commit reaches the budget. Boundary stays `0547131`. #21 round 11 resolved in `1e85546`, except two notes (dead `if (error)` branches, old-bundle 404s).
- T7c + T8 slice `0547131..7749c8f` (medium: `infra/compose.yaml`; 1388 lines, `slice_budget_reached`; standing grant, new feature): parent spot check `npm test -- --run` 70 passed, `npm run size` 155.1 kB / 200 kB (docs/07:15,149 confirm the budget is the initial bundle, so the `index-*` glob is correct). Lineage `review-528c994a56ae8f59`, reliability lens, approved and acknowledged (authority burned). Three WARNINGs (`AbortSignal.any` without fallback; vacuous timeout test; create→list refetch unproved) and four suggestions: #21 round 12, fixed by T8b. Reviewed boundary is now `7749c8f`. Owner decision (2026-09-24): map tiles come from public OpenStreetMap in every profile; T8b adds the row to ADR-0021 and checks the OSM attribution.
- T8b slice `7749c8f..37359b2` (medium, 474 lines, `slice_budget_reached`; owner granted): parent spot check `npm test -- --run` 80 passed. Lineage `review-b01ed8d3bc48fa89`, reliability lens, approved and acknowledged (authority burned). #21 round 12 resolved in `4c1069b`. One WARNING (timeout test leaks fake timers on failure) and one suggestion (fallback test weak): #21 round 13, folded into T9. Reviewed boundary is now `37359b2`.
- Whole-branch stop-hook candidate `a912e8f..431d75b` (owner granted): refused by `lens_context_budget_exceeded` (4451 lines), no authority created; covered by the per-slice reviews above.
- Local only: `.impeccable/surfaces/config.local.json` is listed in `.git/info/exclude` so RDD candidate selection ignores it.
- T5b `47f0b83..658e5b9` (fix, no migration, 141 lines): #21 round 7 resolved in `658e5b9`. RDD
  assessment/acknowledgement for this commit not run by this writer — left to the parent
  orchestrator; boundary not advanced here.
- T6 `907bde2..97127e2` (new migration, 1063 lines, likely `slice_budget_reached`): RDD
  assessment/acknowledgement not run by this writer — left to the parent orchestrator; boundary
  not advanced here.
- T6b `b0c3b55..6dda806` (fix, migration edited in place, 164 lines): #21 round 8 resolved in
  `6dda806`. RDD assessment/acknowledgement for this commit not run by this writer — left to the
  parent orchestrator; boundary not advanced here.

## Acceptance criteria
- [x] A user creates a farm and a plot from a drawn polygon; `area_ha` comes from the geometry.
  Proved by T8: `CreateFarmSheet`/`CreatePlotSheet` never send `area_ha`; a live
  `POST /farms/{id}/plots` smoke call returned a server-computed `area_ha` for a
  client-supplied polygon (see T8 progress).
- [ ] Rainfed plots reject irrigation efficiency and flow (DB `CHECK` and 422) — the DB `CHECK`
  and 422 path were built in T2/T2b/T3b; T8's own web form never sends efficiency/flow for
  `none`, proved live, but this checklist item is server scope, already provable before T8.
- [x] Soil autofill fills θFC/θWP from SoilGrids, or from the FAO-56 texture table with `source = fao56_texture`.
- [x] `GET /crops` returns stages, Kc and `kc_source`.
- [x] A plot holds at most one active crop cycle.
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

- 2026-09-23: T5b done by a delegated `sonnet-high` writer. Resolves GitHub issue #21 round 7 (the
  two T5 review WARNINGs: a nested malformed SoilGrids body could still crash to a raw 500; the
  centroid lon/lat query was unproved).
  - `farms/adapters/soilgrids.py` `IsricSoilGridsAdapter.fetch_sample`: the `response.json()[...]`
    parse and the `SoilGridsSample(...)` construction now share one `try`/`except (ValueError,
    KeyError, TypeError, AttributeError)` block — a single parsing boundary, not a per-field guard
    (task instruction, ponytail). Root cause: `layers = response.json()["properties"]["layers"]`
    only raised on a missing/wrong-typed `properties`/`layers` *key*; an explicit `null` or a
    wrongly-shaped nested value (`unit_measure`, `depths`, `values`, a non-dict layer/depth entry,
    a non-numeric `mean`/`d_factor`) passed that line without raising and then crashed later inside
    `_extract_conventional` or `float(...)`, outside the original `try`, as an uncaught
    `TypeError`/`AttributeError`/`ValueError` (a raw 500 through FastAPI's default handler).
    Widening the `try` to wrap the whole parse, and adding `AttributeError` to the caught types,
    fixes every shape at once without touching `_extract_conventional`.
  - `tests/farms/test_soilgrids_adapter.py`: `_MALFORMED_BODIES`, a parametrized
    `test_fetch_sample_raises_soil_grids_unavailable_on_every_malformed_shape` over 10 shapes (null
    `layers`; `layers` as a dict; a string/number layer entry; null `unit_measure`; null `depths`;
    a string depth entry; null `values`; a non-numeric `mean`; a non-numeric `d_factor`) — each
    must raise `SoilGridsUnavailableError` with `upstream_status == 200`.
  - `tests/farms/test_api.py`: `test_autofilling_soil_queries_soilgrids_at_the_plots_centroid`
    captures the outgoing `httpx.Request` through a `MockTransport` handler and asserts
    `lon == -74.095`, `lat == 10.905` (the axis-aligned `_POLYGON` fixture's known PostGIS
    centroid — lon and lat are distinct enough that a swap fails the assertion), the full
    `property` list, `depth == ["0-5cm"]`, `value == ["mean"]`.
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/). RED
    observed with `git stash push -- src/techcamp/farms/adapters/soilgrids.py` (keeping the new/
    changed tests): `uv run pytest -q tests/farms/test_soilgrids_adapter.py tests/farms/test_api.py
    -k "malformed or centroid"` → `10 failed, 2 passed` — each malformed-shape case failing with
    the actual pre-fix crash (`TypeError: 'NoneType' object is not iterable`,
    `AttributeError: 'str'/'NoneType'/'int' object has no attribute 'get'`,
    `ValueError: could not convert string to float`), the centroid test already passing (it
    doesn't depend on the fix). `git stash pop` restored the fix: GREEN, same selection →
    `12 passed`. REFACTOR: `ruff format` reformatted one multi-line dict literal in the new
    parametrize table.
  - Decisions: no new decisions; this is a bug fix within T5's existing design (`upstream_status`
    stays the HTTP status of the successful-but-malformed response, `200`, consistent with the
    existing 502-vs-503 convention already documented on `SoilGridsUnavailableError`).
  - Verification (server/): `uv run pytest -q` → `180 passed, 2 warnings`; `uv run ruff check .` →
    `All checks passed!`; `uv run ruff format --check .` → all files formatted (1 file reformatted
    on the first pass); `uv run mypy` → `Success: no issues found in 78 source files`; `uv run
    lint-imports` → `Hexagonal layers per module KEPT, 1 kept, 0 broken`.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start; no migration in
    this task, so no `alembic upgrade`/`downgrade` cycle to verify.
  - Commit: `658e5b9` — `fix(farms): map malformed SoilGrids bodies to 502 and prove the centroid
    query`. Authored lines (`git diff --stat` for this commit's files, excluding
    `server/uv.lock`): 141 insertions, 13 deletions across 3 files (0 new, 3 modified) — above the
    ~100 forecast, expected given the 10-shape parametrized table.
  - Doc gap carried from T1-T5, unchanged: `farm.municipality_code` is plain `text`, not yet a real
    FK.

- 2026-09-23: T6 done by a delegated `sonnet-high` writer. `farms` module additions:
  `domain/models.py` (`CropCycleStatus`, `CropCycle`, `compute_expected_harvest_on`,
  `ensure_valid_cycle_status_transition`), `domain/errors.py` (`CropCycleNotFoundError`,
  `CropNotFoundError`, `ActiveCropCycleExistsError`, `InvalidCropCycleTransitionError`),
  `adapters/orm.py` (`CropCycleRow`: `ck_crop_cycle_status`, partial unique index
  `uq_crop_cycle_active_per_plot` on `plot_id` `WHERE status = 'active'`), `adapters/repositories.py`
  (`SqlAlchemyCropRepository.get`, new `SqlAlchemyCropCycleRepository`: `create`,
  `get_active_for_plot`, `get_for_orgs` joined through `plot.org_id`, `update`),
  `application/ports.py` (`CropRepository`, `CropCycleRepository`), `application/manage_cycles.py`
  (new: `create_cycle`, `resolve_cycle_access`, `update_cycle`), `adapters/api/deps.py`
  (`CropCycleRepoDep`), `adapters/api/router.py` (`POST /plots/{plot_id}/cycles`,
  `PATCH /cycles/{cycle_id}`). Migration `ff21853418b8` (chained off `7f9c1b3cae7f`).
  - Decisions:
    - `expected_harvest_on` is derived, not accepted from the client: `POST
      /plots/{plot_id}/cycles`'s body (docs/04-api.md:56) only lists `{crop_id, sown_on}`, so
      `compute_expected_harvest_on` sums the crop's `crop_stage.length_days` (T3's FAO-56 split)
      from `sown_on`. `None` for a crop with no stages (`kc_source = none`, e.g. yam) — nothing to
      sum, same as its missing Kc. `PATCH /cycles/{cycle_id}` can still set/override it explicitly
      (its own documented field), same partial-update convention as `patch_plot`/`patch_farm`
      (`exclude_unset` + `dataclasses.replace`).
    - One active cycle per plot: docs/00-glosario.md states the rule; enforced by a DB partial
      unique index (`uq_crop_cycle_active_per_plot`, `WHERE status = 'active'`) as the source of
      truth, per docs/03's "invariants are CHECK constraints, not only application code" rule.
      `create_cycle` also runs an application-layer precheck
      (`cycles.get_active_for_plot`) so the conflict surfaces as `409`, not a raw `IntegrityError`
      500 — a small, accepted race window between two concurrent creates on the same plot
      (ponytail: no existing pattern in this module for translating a DB constraint violation into
      a domain error at the adapter layer, so the precheck is the shorter diff).
    - Conflict status code: docs/04-api.md is silent on 409 vs. 422 for this case. T6 decision:
      `409` — an existing resource state conflicts with the request, not a malformed request.
    - `crop_id` validity: docs/04-api.md doesn't say what happens for an unknown `crop_id`. T6
      decision: `422` (`CropNotFoundError`), the same treatment as `InvalidTechnicianError` — a bad
      referenced id on an input field, not the endpoint's own resource (which stays `404` for the
      plot).
    - Status transitions: docs/04-api.md's `PATCH { status? }` doesn't enumerate allowed
      transitions. Task instruction: "only active -> harvested|lost unless docs say otherwise" —
      docs are silent, so that's the whole table (`ensure_valid_cycle_status_transition`).
      `harvested`/`lost` are terminal, and re-sending the same `active` status is rejected too (no
      no-op exception documented). An explicit `null` on `status` is `422` via the existing
      `_reject_explicit_null` helper (reused, not reimplemented); `expected_harvest_on` is the only
      nullable field on the patch.
    - `crop_cycle` has no `org_id` column (matches docs/03's field list exactly): access is gated
      through the plot everywhere, same reasoning as `soil_profile` (T4). `resolve_cycle_access`
      mirrors `resolve_farm_access`/`resolve_plot_access`: fetch the caller's memberships, look up
      the cycle by `org_ids` (joined through `plot.org_id` in the repository), then resolve its
      role through the plot's own org.
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/). RED
    observed by moving the new `application/manage_cycles.py` aside and `git stash`-ing the seven
    modified implementation files (domain/errors.py, domain/models.py, adapters/orm.py,
    adapters/repositories.py, adapters/api/deps.py, adapters/api/router.py, application/ports.py),
    keeping the new migration and the new/changed tests: `uv run pytest -q
    tests/farms/test_domain_models.py tests/farms/test_repositories.py` → 2 collection errors
    (`ImportError: cannot import name 'InvalidCropCycleTransitionError'`; `ImportError: cannot
    import name 'SqlAlchemyCropCycleRepository'`); `uv run pytest -q tests/farms/test_api.py -k
    cycle` → `13 failed` (404 "Not Found" — the routes didn't exist — where 201/200/409/422/403
    were expected, and a `KeyError: 'id'` in every `PATCH` test's setup once `POST` stopped
    returning a cycle). Restored the implementation: GREEN, same selections → `24 passed`; full
    `uv run pytest -q` → `207 passed`. REFACTOR: three `ruff format`/E501 line-length fixes (a
    `SqlAlchemyCropRepository.get` query split onto two statements, two test helper signatures
    wrapped).
  - Verification (server/): `uv run pytest -q` → `207 passed, 2 warnings`; `uv run ruff check .` →
    `All checks passed!`; `uv run ruff format --check .` → `102 files already formatted`; `uv run
    mypy` → `Success: no issues found in 79 source files`; `uv run lint-imports` → `Hexagonal
    layers per module KEPT, 1 kept, 0 broken`; `uv run alembic upgrade head` → applied
    `ff21853418b8`; `uv run alembic downgrade -1` → reverted it; `uv run alembic upgrade head` →
    reapplied clean.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start.
  - Commit: `97127e2` — `feat(farms): add crop cycles with one active cycle per plot`. Authored
    lines (`git diff --stat --cached -- . ':!server/uv.lock'`): 1063 insertions, 6 deletions
    across 12 files (2 new, 10 modified) — well above the ~250 forecast and the ~400-line delivery
    heuristic in a single commit, same reason as T2/T2b/T3b/T4/T5: one work-unit commit was
    specified for this task, and the full port+repository+application+router stack plus three test
    files (~24 new tests across domain/repository/API layers) don't split smaller within a single
    task. Flagging for the owner/parent orchestrator's delivery-strategy decision, not re-split
    here.
  - Doc gap carried from T1-T5, unchanged: `farm.municipality_code` is plain `text`, not yet a
    real FK.

- 2026-09-23: T6b done by a delegated `sonnet-high` writer. Resolves GitHub issue #21 round 8 (the
  two T5b+T6 review WARNINGs: concurrent cycle create → raw 500 instead of 409; harvest date
  before sowing silently accepted) plus the two open suggestions (missing 201 assert in
  `_create_cycle`; SoilGrids parse exception not logged).
  - `farms/adapters/repositories.py` `SqlAlchemyCropCycleRepository.create`: wraps
    `self._session.commit()` in `try`/`except IntegrityError`. asyncpg's driver-level exception
    carries no `constraint_name` attribute by the time SQLAlchemy's asyncpg dialect re-wraps it
    (verified interactively: only `sqlstate`/`pgcode` survive, `23505` is the generic
    `unique_violation` SQLSTATE) — matched the constraint name in the wrapped exception's message
    text instead (`"uq_crop_cycle_active_per_plot" in str(exc.orig)`), scoped to that one
    constraint so any other integrity error (e.g. a bad `crop_id` FK) still propagates unchanged.
    On a match: rollback, then raise the existing `ActiveCropCycleExistsError(plot_id)` (already
    mapped to `409` by the router since T6) — `create_cycle`'s application-layer precheck
    (`get_active_for_plot`) stays as the normal path; this is the DB-side backstop for the race
    window between two concurrent creates on the same plot.
  - `farms/domain/errors.py`: `HarvestBeforeSowingError` (same pattern as
    `RainfedPlotHasIrrigationError`: a domain error backed by a DB `CHECK`).
  - `farms/domain/models.py`: `ensure_harvest_not_before_sowing(sown_on, expected_harvest_on)` —
    pure, rejects a non-null `expected_harvest_on` earlier than `sown_on`.
  - `farms/application/manage_cycles.py` `update_cycle`: calls the new domain rule only when
    `expected_harvest_on` is in the PATCH `changes` (same style as the existing
    `"status" in changes` guard); `sown_on` itself isn't patchable, so `merged.sown_on` always
    equals the stored value.
  - `farms/adapters/api/router.py` `patch_cycle`: maps `HarvestBeforeSowingError` → `422`
    problem+json.
  - `migrations/versions/ff21853418b8_add_crop_cycles.py` (edited in place — this branch's own
    unmerged migration, per the task instruction) and `farms/adapters/orm.py` `CropCycleRow`: add
    `CheckConstraint("expected_harvest_on is null or expected_harvest_on >= sown_on",
    name="ck_crop_cycle_harvest_not_before_sowing")`, matching the ORM/migration pairing pattern
    every other farms table already uses.
  - `farms/adapters/soilgrids.py` `IsricSoilGridsAdapter.fetch_sample`: the malformed-body
    `except (ValueError, KeyError, TypeError, AttributeError)` block already chained
    (`raise ... from exc`, from T5b) — added a module-level `logger = logging.getLogger(__name__)`
    (no existing logging call anywhere in `server/src` to match; this is the standard stdlib
    idiom) and `logger.warning("malformed SoilGrids response body: %s", exc)` before the raise.
  - `tests/farms/test_api.py` `_create_cycle`: added `assert created.status_code == 201,
    created.text` before parsing the response body (matches the same helpers' existing pattern
    for `_create_farm`/`_create_plot`).
  - Tests: `test_a_second_active_cycle_on_the_same_plot_raises_active_crop_cycle_exists_error`
    (renamed/updated from the old `..._is_rejected_by_the_database`, now asserts the translated
    domain error and its `plot_id`, `test_repositories.py`) calls `repo.create` twice directly —
    the task's suggested race simulation, bypassing `create_cycle`'s precheck; new
    `test_creating_a_cycle_with_a_bad_crop_id_still_raises_integrity_error` proves an unrelated
    integrity error still propagates raw; new
    `test_a_harvest_date_before_the_sown_date_is_rejected_by_the_database` proves the DB `CHECK`;
    new `test_ensure_harvest_not_before_sowing_allows_none_or_on_or_after_sowing` and
    `..._rejects_a_harvest_date_before_sowing` (`test_domain_models.py`); new
    `test_patching_a_cycles_expected_harvest_on_before_sown_on_is_422` (`test_api.py`); added an
    `exc_info.value.__cause__ is not None` assertion to the existing
    `test_fetch_sample_raises_on_a_malformed_response_body` (`test_soilgrids_adapter.py`) to prove
    the chain survives.
  - Decisions: no new decisions beyond the `sqlstate`+message-text matching above (forced by
    what the asyncpg/SQLAlchemy dialect actually preserves, verified interactively against real
    Postgres rather than assumed).
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner `uv run pytest` (server/).
    RED observed by `git stash push` of the eight implementation files (domain/errors.py,
    domain/models.py, manage_cycles.py, router.py, repositories.py, orm.py, soilgrids.py, the
    migration), keeping the new/changed tests: `test_domain_models.py` → 1 collection error
    (`ImportError: cannot import name 'HarvestBeforeSowingError'`);
    `tests/farms/test_repositories.py -k "crop_cycle_exists_error or bad_crop_id or
    harvest_date_before"` → 2 failed (a raw `IntegrityError` instead of
    `ActiveCropCycleExistsError`; `DID NOT RAISE IntegrityError` for the CHECK, since it didn't
    exist yet); `tests/farms/test_api.py -k expected_harvest_on_before_sown_on` → 1 failed (`200`
    where `422` was expected). `git stash pop` restored the implementation: GREEN, `uv run pytest
    -q` → `212 passed`. One further RED found mid-implementation: the repository fix's first
    attempt matched `exc.orig.constraint_name`, which doesn't exist on the wrapped asyncpg
    exception SQLAlchemy's asyncpg dialect produces (verified interactively: only
    `sqlstate`/`pgcode` survive) — the still-failing
    `test_a_second_active_cycle_on_the_same_plot_raises_active_crop_cycle_exists_error` caught it;
    fixed by matching `sqlstate == "23505"` plus the constraint name in the message text. REFACTOR:
    `ruff format` reformatted one boolean expression in the fixed repository method.
  - Verification (server/): `uv run pytest -q` → `212 passed, 2 warnings`; `uv run ruff check .`
    → `All checks passed!`; `uv run ruff format --check .` → `1 file reformatted` on the first
    pass, all formatted after; `uv run mypy` → `Success: no issues found in 79 source files`;
    `uv run lint-imports` → `Hexagonal layers per module KEPT, 1 kept, 0 broken`; `uv run alembic
    upgrade head` → applied through `ff21853418b8` (edited in place); `uv run alembic downgrade
    -1` → reverted it; `uv run alembic upgrade head` → reapplied clean.
  - Postgres was already running (`infra_postgres_1`, healthy) at session start.
  - Commit: `6dda806` — `fix(farms): return 409 on concurrent cycle creates and reject harvest
    before sowing`. Authored lines (`git diff --stat` for this commit's files, excluding
    `server/uv.lock`): 164 insertions, 5 deletions across 12 files (0 new, 12 modified) — within
    the delivery heuristic and close to the ~120 forecast.
  - Doc gap carried from T1-T6, unchanged: `farm.municipality_code` is plain `text`, not yet a
    real FK.

- 2026-09-23: T7 done by a delegated `sonnet-high` writer on branch
  `feat/e3-farms-web` (from `main` @ `a912e8f`, backend already merged via
  #22–#27). `web/src/lib/api/` (shared, per docs/07's "lo compartido baja a
  design-system/ o lib/"): `client.ts` (`apiFetch`, `ApiError` — base URL,
  bearer header, JSON body, problem+json parsing), `session.ts` (token/org id
  in `localStorage`, `useToken`/`useOrgId` via `useSyncExternalStore`),
  `useApiResource.ts` (loading/error/success hook). `web/src/features/auth/`:
  `api/authApi.ts` (`requestOtp`, `verifyOtp`, `fetchMe`),
  `containers/SignInScreen.tsx` (phone → code → org-pick steps),
  `components/SignOutButton.tsx`, `guard.ts` (`requireAuthLoader`, redirects
  to `/ingreso`). `web/src/features/plots/`: `api/plotsApi.ts`
  (`loadFarmsWithPlots`: `GET /farms?org_id=` then `GET
  /farms/{id}/plots` per farm), `components/PlotsList.tsx` (presentational),
  `containers/PlotsScreen.tsx` (loading/error/empty states, replaces the
  `parcelas` tab's `PlaceholderPage`). `app/routes.tsx`: `/ingreso` route,
  `requireAuthLoader` on the `/` (`AppShell`) route so every tab redirects an
  unauthenticated visitor. `app/PlaceholderPage.tsx`: optional `children`
  (used to add `SignOutButton` to the `Más` tab without a new screen).
  `vite.config.ts`: dev proxy for the api's actual top-level path prefixes.
  - Decisions:
    - Token storage: ADR-0014 and docs/05 don't say where the PWA keeps the
      token (owner fallback instruction). Used `localStorage` (two keys,
      token + org id) so a refresh keeps the session.
    - No TanStack Query: docs/05 names it as the server-state layer, but
      nothing installs it yet and this task's own scope is "a small fetch
      wrapper". A one-shot GET list has no caching/invalidation need today,
      so `useApiResource` (plain `useState`/`useEffect`) covers it without a
      new dependency. Flagged for the owner: adopt TanStack Query when T8/T9
      need its cache invalidation or offline persistence, and replace this
      hook then — not attempted here to avoid inventing the migration path
      speculatively.
    - No `openapi-typescript`/`openapi-fetch`: docs/05 names generated types
      from OpenAPI, but there's no OpenAPI schema-generation step wired yet
      and the task explicitly scoped T7 to "a small fetch wrapper". Types for
      the four endpoints used are hand-written in `authApi.ts`/`plotsApi.ts`
      instead. Flagged as a gap, not invented as a bigger codegen setup.
    - Vite dev proxy: `main.py` registers routes with no common prefix (`/me`,
      `/organizations`, `/farms`, `/crops`, `/plots`, `/cycles`, `/dev`,
      `/health`), not the `/api/v1` docs/04-api.md names — a doc/code gap
      carried from earlier tasks (unaddressed here, out of T7's scope).
      `vite.config.ts` proxies each of those exact prefixes to
      `http://localhost:8000` (or `VITE_API_URL`) instead of one shared
      `/api` prefix, matching what the backend actually serves; verified live
      (see Verification).
    - Org chooser labels: `GET /me`'s `MeResponse` only returns
      `memberships: [{org_id, role}]`, no organization name (no such field
      reachable from that endpoint) — flagged, not invented: the chooser
      shows a shortened org id plus the Spanish role label
      (`Organización 3f9a2c1b · Propietario`) until an org-name source
      exists.
    - `apiFetch` error parsing handles two response shapes: the documented
      problem+json (`type/title/status/detail`, from `ProblemError`) and
      FastAPI's own default `RequestValidationError` handler
      (`shared/errors.py`), which returns `{"detail": [{msg, ...}]}` with a
      plain `application/json` content type — not the documented shape. This
      is a real doc/code gap in the existing backend (not introduced here);
      the client is defensive against it since a malformed `org_id` or
      similar framework-level 422 would otherwise show as an unparsed error.
    - User-facing error copy: raw backend error titles stay in English
      (`ProblemError.title`, e.g. "Invalid or expired code") since backend
      code is English-only per `AGENTS.md`. The sign-in screen maps the one
      error case it distinguishes (401 on verify) to a fixed Spanish message;
      the plots screen shows one fixed Spanish message for any load failure.
      Full backend-error-message localization is out of scope and flagged,
      not attempted.
    - Irrigation system label: rendered as plain text ("Secano"/"Goteo"/…),
      not `StatusBadge` — `status.ts`'s `StatusState` (`ok/watch/irrigate/
      stress`) is the water-balance vocabulary only; docs/07's "Two
      Vocabularies Rule" (also stated in `.impeccable/design.json`) bars
      reusing it for irrigation system type.
    - `PlaceholderPage` gained an optional `children` prop (backward
      compatible) instead of a new `MasScreen`: the only thing needed on the
      `Más` tab for T7 is a sign-out control, and a full "Más" screen isn't
      in this epic's scope.
  - TDD: mode on, source `AGENTS.md`/owner decision 2026-09-22, runner
    `npm test -- --run` (`web/`). RED observed by moving the ten new
    implementation files (not the new/changed tests) to a temp location and
    running the full suite: 5 test files failed with `Failed to resolve
    import` (Vite import-analysis) for the missing modules —
    `App.test.tsx`, `routes.test.tsx` (via `routes.tsx`'s now-broken
    imports), `client.test.ts`, `SignInScreen.test.tsx`,
    `PlotsScreen.test.tsx` — 6 unrelated test files still passed (22 passed,
    5 failed). Restored the files: GREEN, `11 passed (11 files), 43 passed`.
    REFACTOR: `useApiResource` initially called `setState` synchronously at
    the top of its effect body, which `eslint-plugin-react-hooks`'s
    `set-state-in-effect` rule (already enabled via
    `reactHooks.configs.recommended.rules`) flagged; rewritten to reset to
    `loading` during render when `deps` changes (React's own "adjusting
    state when a prop changes" pattern) instead of inside the effect.
  - Verification (`web/`): `npm run lint` → clean (0 errors after the
    `useApiResource` refactor above; 1 error before it); `npm run typecheck`
    → clean; `npm test -- --run` → `11 passed (11), 43 passed (43)`;
    `npm run build` → built, PWA precache 13 entries; `npm run size` →
    `139.07 kB` gzip (limit 200 kB).
  - Manual smoke check: ran the real stack — `uv run uvicorn
    techcamp.main:app` (seminar profile) against the already-running dev
    Postgres, and `npm run dev` (Vite). Seeded one organization, user,
    membership, farm and plot directly in Postgres (no scenario/seed loader
    exists yet for this epic). Drove the exact request chain the UI makes,
    through the Vite dev proxy (`curl` against `localhost:5173`, not the UI
    itself — no browser automation tool was used): `POST /dev/auth/otp` →
    204, read the printed code from the api log, `POST
    /dev/auth/otp/verify` → token, `GET /me` → the seeded membership, `GET
    /farms?org_id=` → the seeded farm, `GET /farms/{id}/plots` → the seeded
    plot (`area_ha` computed by Postgres, `irrigation_system: "drip"`). All
    six calls returned the expected status and body through the proxy,
    confirming the path-prefix proxy config is correct end to end. Cleaned
    up the seeded rows and stopped both processes afterward; a pre-existing,
    unrelated organization/user row from earlier work was left untouched.
    The UI itself (React rendering in a real browser) wasn't driven — that's
    covered by the Vitest component tests with mocked `fetch` using the same
    request shapes proven live here.
  - Commit: `99c5d47` — `feat(web): add OTP sign-in and plots list for E3`.
    Authored lines (`git diff --cached --stat -- . ':!web/package-lock.json'`
    for this commit's files): 988 insertions, 7 deletions across 17 files
    (13 new, 4 modified) — well above the ~250 forecast, same reason as
    several backend tasks in this epic: one work-unit commit was specified,
    and a full data layer (client + session + hook) plus two complete
    features (3-step auth flow, plots list) with their tests don't split
    smaller within a single task. Flagging for the owner/parent
    orchestrator's delivery-strategy decision, not re-split here. RDD
    assessment/acknowledgement for this commit not run by this writer — left
    to the parent orchestrator; boundary not advanced here.
  - Doc gaps flagged (new, beyond the carried `municipality_code` one from
    T1–T6, which this task doesn't touch): (1) docs/05 names TanStack Query
    and `openapi-typescript`/`openapi-fetch`; T7 uses neither, per its own
    "small fetch wrapper" scoping — see Decisions above. (2) docs/04-api.md's
    endpoint list is missing `POST /dev/auth/otp/verify` (implemented,
    undocumented). (3) The api has no `/api/v1` prefix docs/04 describes.
    (4) `GET /me` has no organization name, only `org_id`/`role`, so the
    multi-org chooser can't show a human-readable name.

- 2026-09-24: T7b done by a delegated `sonnet-high` writer on branch
  `feat/e3-farms-web` @ `99c5d47` (T7 done). Two work-unit commits, per the
  task's own split (server, then web).

  **Commit 1 (server): `refactor(api): serve the REST API under /api/v1`.**
  `main.py`: `app.include_router(..., prefix="/api/v1")` for identity, farms,
  and the seminar `dev_auth_router` (the `/dev` routes are part of the
  versioned REST API too, not a separate operational surface). `/health`
  stays unprefixed — decision: it's an operational probe (docs/04-api.md's
  "Operación" section lists `/healthz`/`/readyz`/`/metrics` separately from
  the `/api/v1` endpoint list), and neither `infra/compose.yaml` nor
  `server/Dockerfile` defines a container healthcheck that pins its path
  either way, so moving it carried no infra risk in either direction.
  `docs/04-api.md`'s `/dev` section gained the `POST /dev/auth/otp/verify`
  line (implemented in E2, never documented).
  - Test paths: the shortest-diff fix (ponytail) was `TestClient(app,
    base_url="http://testserver/api/v1")` (verified empirically that httpx's
    `base_url` + a relative path join correctly) in the 5 test files that hit
    non-`/health` endpoints (`tests/farms/test_api.py`,
    `tests/identity/test_dev_auth.py`, `test_me.py`, `test_org_isolation.py`,
    `test_sign_in_flow.py`) — no shared `client` fixture existed to change in
    one place. `tests/test_health.py` keeps `TestClient(app)` unprefixed.
  - TDD: mode on, source `AGENTS.md`/owner decision 2026-09-22, runner
    `uv run pytest` (server/). RED observed by mounting the prefix without
    touching tests yet: `uv run pytest -q` → `82 failed, 130 passed` (404s
    on every moved path, plus one `content-type` mismatch from the shared
    `RequestValidationError` handler on an unrelated 404 body). Updated the
    5 test files: GREEN, `uv run pytest -q` → `212 passed`. REFACTOR: none
    needed.
  - Verification (server/): `uv run pytest -q` → `212 passed, 2 warnings`;
    `uv run ruff check .` → `All checks passed!`; `uv run ruff format
    --check .` → `102 files already formatted`; `uv run mypy` → `Success: no
    issues found in 79 source files`; `uv run lint-imports` → `Hexagonal
    layers per module KEPT, 1 kept, 0 broken`.
  - Commit: `f3a7992` — `refactor(api): serve the REST API under /api/v1`.
    Authored lines (`git diff --cached --stat -- . ':!server/uv.lock'`): 88
    insertions, 78 deletions across 7 files.

  **Commit 2 (web): `refactor(web): use generated OpenAPI client and
  TanStack Query`, `Refs #21`.**
  Added `@tanstack/react-query`, `openapi-fetch` (deps) and
  `openapi-typescript` (dev dep, docs/05:179-180); `npm run gen:api` prints
  the FastAPI app's own `app.openapi()` to a temp file and generates
  `web/src/lib/api/schema.d.ts` from it (committed). `web/src/lib/api/
  client.ts` rewritten around `openapi-fetch`'s `createClient<paths>` with
  one middleware (`sessionMiddleware`) doing bearer-token injection, a 10s
  `AbortSignal.timeout` per request (a fresh `Request` per call — request
  bodies survive that re-wrap, verified interactively), and centralized
  error handling (parses problem+json and FastAPI's own validation-error
  shape into the existing `ApiError`, same as T7). Deleted
  `useApiResource.ts` (ponytail: delete what the library replaces) and the
  dead `useToken` from `session.ts`. New `queryClient.ts`
  (`retry: false` — this app's own retry UX is the "Reintentar" button, not
  TanStack Query's automatic retries) wired into `App.tsx` via
  `QueryClientProvider`. New `errorCopy.ts` (`describeApiError`): network/
  timeout (`TypeError`/`DOMException`) → "Revisa tu conexión…", anything
  else → a generic message. `plotsApi.ts`: `useFarms` (`useQuery`, first
  page only, `hasMore` from `next_cursor`) and `usePlotsByFarm`
  (`useQueries`, one query per farm id) replace the old combined
  `Promise.all` load — a single farm's plots query fails independently, so
  `PlotsList`/`PlotsScreen` render every other farm normally (#21 round 10).
  `authApi.ts`/`SignInScreen.tsx`: same request shapes, reuse the generated
  `components["schemas"]["MeResponse"|"MembershipView"]` types instead of
  hand-written ones; "Cambiar número" now clears both the code and the error
  (`handleChangeNumber`); error copy in both catch blocks routes through
  `describeApiError` for the non-401 case. `vite.config.ts`: one proxy entry
  (`/api/v1` → `VITE_API_URL` or `localhost:8000`) replaces the 8-prefix
  array; the browser always calls the relative `/api/v1`.
  - Decisions:
    - 401 handling is global and request-scoped, not endpoint-specific:
      `sessionMiddleware.onResponse` clears the session and
      `window.location.assign('/ingreso')` only when the *failing* request
      itself carried an `Authorization` header. An anonymous call (OTP
      request/verify, before any session exists) returning 401 is a normal
      in-flow error the screen already handles (wrong code) — this is what
      distinguishes it from an authenticated call's 401 (expired/invalid
      token), without an allowlist of "auth endpoints".
    - Atomic sign-in session: unchanged from T7 — `setSession` already only
      runs after `verifyOtp` succeeds, and `setOrgId` only runs after `GET
      /me` returns at least one membership (re-verified, not re-implemented;
      T7's review WARNING was about the *old* client never recovering from a
      401 afterward, not about this ordering).
    - Farms pagination: implemented as a `hasMore` flag + a note ("Tu
      organización tiene más fincas de las que se muestran aquí."), not a
      full pager — T7b's brief explicitly allows "clearly shows that more
      exist" as the alternative; a real cursor pager belongs with the fuller
      plots UI in T8/T9.
    - `openapi-typescript`'s peer range (`typescript ^5.x`) lags this repo's
      `typescript@6.0.3` (confirmed: no released or `next`-tagged version
      changes this). `web/.npmrc` sets `legacy-peer-deps=true` (verified
      both `npm install` and `npm ci` succeed with it). That flag also
      disables npm's automatic peer-dependency install project-wide — it
      silently dropped `@testing-library/dom` (an undeclared peer of
      `@testing-library/react`), which broke `screen`/`fireEvent`/`waitFor`
      imports; added it back explicitly as a devDependency rather than
      dropping `legacy-peer-deps` (no released fix exists for the root
      conflict). Caught by `npm run typecheck`, confirmed pre-existing
      unrelated to this task's own diff by reproducing on a `git stash`.
    - `apiClient` passes `fetch: (...args) => globalThis.fetch(...args)` to
      `createClient` instead of relying on its default: `createClient`
      captures whatever `globalThis.fetch` is once, at module import time,
      which in tests is *before* `vi.stubGlobal('fetch', ...)` ever runs
      (`apiClient` is a module-level singleton) — every mocked-fetch test
      was hitting the real network until this fix. No behavior change
      outside tests (`globalThis.fetch` never changes in the real app).
    - Test environment: Node's native `fetch`/`Request` need an absolute
      URL (no page origin exists under Vitest), unlike a real browser which
      resolves the app's actual relative `''` base URL against its own
      origin — `vite.config.ts`'s `test.env` sets `VITE_API_URL` to
      `http://localhost` for the test run only.
  - TDD: mode on, source `AGENTS.md`/owner decision 2026-09-22, runner
    `npm test -- --run` (web/). RED observed per behavior, incrementally
    while porting each file off `apiFetch`/`useApiResource` (`vitest` failed
    with `Invalid URL`/`ECONNREFUSED`/`Cannot redefine property: assign`
    errors before the `fetch` indirection, `test.env`, and
    `vi.stubGlobal('location', …)` fixes above — each one a real RED against
    the new implementation, not a placeholder). Final GREEN:
    `npm test -- --run` → `11 passed (11), 47 passed (47)`. New tests:
    `client.test.ts` (bearer token, 204, problem+json, FastAPI validation
    shape, 401 sign-out+redirect, 401 without a session does *not* sign
    out); `PlotsScreen.test.tsx` (retry after a farms-list error, the
    "more farms" note, one farm's plots failing without blanking the
    others); `SignInScreen.test.tsx` ("Cambiar número" clears the error and
    the code). REFACTOR: none needed beyond what the fixes above already
    settled.
  - Verification (web/): `npm run lint` → clean; `npm run typecheck` →
    clean; `npm test -- --run` → `11 passed (11), 47 passed (47)`;
    `npm run build` → built, PWA precache 13 entries; `npm run size` →
    `151.57 kB` gzip (limit 200 kB, up from T7's 139.07 kB).
  - Manual smoke check: real stack — `uv run alembic upgrade head` on the
    (previously unmigrated) dev Postgres, `uv run uvicorn techcamp.main:app`
    (seminar profile), `npm run dev` (Vite). Seeded one organization, user,
    membership, farm and plot directly in Postgres. Drove the same six-call
    chain T7 proved, now against `/api/v1` through the Vite proxy (`curl`
    against `localhost:5173`): `POST /api/v1/dev/auth/otp` → 204, `POST
    /api/v1/dev/auth/otp/verify` → token, `GET /api/v1/me` → the seeded
    membership, `GET /api/v1/farms?org_id=` → the seeded farm, `GET
    /api/v1/farms/{id}/plots` → the seeded plot, `GET /api/v1/me` with a
    bogus bearer → `401` problem+json (the shape `sessionMiddleware`
    recovers from). All six calls returned the expected status and body
    through the single `/api/v1` proxy entry. Cleaned up the seeded rows,
    ran `uv run alembic downgrade base` to restore the dev database to the
    unmigrated state it was found in, and stopped both processes afterward.
  - Commit: `c7d30f3` — `refactor(web): use generated OpenAPI client and
    TanStack Query`. Authored lines (`git diff --cached --stat -- . ':!web/
    package-lock.json' ':!web/src/lib/api/schema.d.ts'`): 430 insertions,
    237 deletions across 17 files — above the ~500 forecast for the task as
    a whole (166 + 667 = 833) but within one work-unit commit per the
    task's own two-commit split; flagging for the owner/parent
    orchestrator's delivery-strategy decision, same as every other task in
    this epic.
  - #21 round 10 resolved in `c7d30f3` (all six T7 review findings: no 401
    recovery, no fetch timeout, token persisted before `/me`, `VITE_API_URL`
    dual use, farms pagination ignored, 401 shown as connection error) plus
    the docs-drift items the parent found (`/api/v1` prefix — resolved in
    `f3a7992`; TanStack Query + `openapi-typescript`/`openapi-fetch` —
    resolved in `c7d30f3`). Nothing from round 10 deferred.
  - Doc gaps carried from T1-T7, unchanged: `farm.municipality_code` is
    plain `text`, not a real FK; `GET /me` still has no organization name
    (the chooser still shows a shortened org id + role).
  - RDD assessment/acknowledgement for both commits not run by this
    writer — left to the parent orchestrator; boundary not advanced here.

## PR plan (stacked-to-main, one PR per task; owner 2026-09-23)
All slices are `size:exception` (each task is one work-unit commit).
- #22 `feat/e3-farms-01-schema` T1 `4684262..8f7c472` (724)
- #23 `feat/e3-farms-02-endpoints` T2 + T2b `..55fbec2` (2163)
- #24 `feat/e3-farms-03-crops` T3 + T3b `..33d7b99` (748)
- #25 `feat/e3-farms-04-soil` T4 `..3fe79d9` (670)
- #26 `feat/e3-farms-05-soilgrids` T5 + T5b `..907bde2` (1033)
- #27 `feat/e3-farms-06-cycles` T6 + T6b `..9c8243e` (1410)

- CI fix (2026-09-24): every PR failed in CI with `type "geometry" does not exist`, because CI's Postgres service does not run `infra/postgres/init-extensions.sql`. Root fix `c0edf39` (on #22): the farm migration runs `CREATE EXTENSION IF NOT EXISTS postgis`. Reproduced on a fresh database without PostGIS (alembic failed), then 212 passed at the chain tip on a fresh database. The chain was restacked with `git rebase --update-refs` and force-pushed, so the commit hashes above changed. CI is green on #22–#27.

- 2026-09-24: T7c done by a delegated `sonnet-high` writer. Resolves GitHub
  issue #21 round 11 (four T7b review WARNINGs; three of the five
  suggestions).
  - `web/src/lib/api/client.ts`: `API_BASE_URL` now reads
    `VITE_API_TEST_BASE_URL` (never `VITE_API_URL`) — the browser base is
    always the relative `''`, only Vitest's `test.env` sets that variable.
    New `isAnonymousRequest`/`ANONYMOUS_PATH_SUFFIXES`: `onRequest` no
    longer attaches a stored bearer token to `/dev/auth/otp` or
    `/dev/auth/otp/verify`, so a stale token can no longer make a wrong-code
    401 look like an expired session (`onResponse`'s existing `hadSession`
    check now sees no `Authorization` header on those calls, same as an
    always-anonymous caller). `onRequest`'s timeout now combines the
    caller's own `request.signal` with `AbortSignal.timeout(10_000)` via
    `AbortSignal.any` instead of discarding it. Comments corrected to match
    (dual-use `VITE_API_URL` comment, `DOMException TimeoutError` instead of
    "`TypeError`-like").
  - `web/vite.config.ts`: the dev-proxy target reads `API_PROXY_TARGET`
    (`process.env`, not `VITE_`-prefixed, so Vite never exposes it to the
    browser bundle) instead of `VITE_API_URL`. `test.env` sets
    `VITE_API_TEST_BASE_URL` instead, a variable of its own — the test base
    no longer reuses the proxy variable.
  - `infra/compose.yaml`: the `web` service's env var renamed
    `VITE_API_URL` → `API_PROXY_TARGET` to match.
  - `web/src/lib/api/errorCopy.ts`: comment corrected — a 401 from an
    anonymous call now legitimately reaches `describeApiError` as a plain
    `ApiError` (round 11), not only network/timeout failures.
  - `web/src/features/plots/components/PlotsList.tsx`: the per-farm error
    state now renders a `Button` (`variant="secondary"`, "Reintentar",
    reused from `design-system/ui/button`) that calls `plotsQuery.refetch()`
    — only that farm's query retries, the rest of the list is untouched.
  - `web/package.json`: `gen:api` now writes to `$(mktemp)` instead of a
    fixed `/tmp` path, and forces `TECHCAMP_PROFILE=seminar` so the
    generated schema always includes the seminar-only `/dev/auth/*` routes
    `authApi.ts` depends on (round 11 suggestions).
  - Decisions:
    - Anonymous-path matching is by `pathname.endsWith(...)` on
      `request.url` (`/dev/auth/otp`, `/dev/auth/otp/verify`), not a request
      method or body sniff: cheapest correct check, matches how
      `authApi.ts`'s two callers are the only source of anonymous requests
      today.
    - `AbortSignal.any` (Node 22 / evergreen browsers, no new dependency)
      over a manual `addEventListener` relay: openapi-fetch already forwards
      `fetchOptions.signal` straight into the `Request` `onRequest` receives
      (verified in `openapi-fetch`'s `coreFetch`, `node_modules/openapi-fetch/src/index.js`),
      so `request.signal` is already the caller's signal (or a permanently-open
      one) and combining it is a one-liner.
    - Left as notes, not fixed (task's own "skip unless trivial" for the
      remaining two suggestions): the dead `if (error)` branches in the API
      wrapper functions (`authApi.ts`, `plotsApi.ts` — `error` is already
      thrown by `sessionMiddleware.onResponse` before openapi-fetch would
      ever return `{data: undefined}` for a non-2xx response, so those
      branches are unreachable but harmless); unprefixed old PWA bundles
      get a hard 404 on the next deploy (a Workbox precache-naming concern,
      out of scope for this round).
  - TDD: mode on, source AGENTS.md/owner decision 2026-09-22, runner
    `npm test -- --run` (web/). RED observed by `git stash push` of the six
    implementation files (`client.ts`, `errorCopy.ts`, `PlotsList.tsx`,
    `vite.config.ts`, `infra/compose.yaml`, `package.json`), keeping the
    new/changed tests: `npm test -- --run` → 5 failed: `does not attach a
    stale stored token to an anonymous OTP verify call` /
    `...request call` (`AssertionError: expected true to be false`, the
    stale token's `Authorization` header was still attached), `does not
    sign out or clear a stale session when a wrong code 401s`
    (`AssertionError: expected null to be 'stale-token'`, the stale session
    was cleared), `preserves a caller-supplied abort signal alongside the
    timeout` (`AssertionError: promise resolved ... instead of rejecting`,
    the caller's pre-aborted signal was discarded), `retries only the
    failed farm's plots when its Reintentar button is clicked`
    (`TestingLibraryElementError: Unable to find an accessible element with
    the role "button" and name "Reintentar"`, no per-farm retry existed).
    The new `errorCopy.test.ts` and the "forwards a request that carries an
    abort signal" test passed unchanged in this RED run — genuinely new
    coverage of already-correct behavior, not a behavior change, per the
    task's own list (`describeApiError`'s classification and the "request
    carries a signal" timeout mechanism both predate this task). `git stash
    pop` restored the implementation: GREEN, `npm test -- --run` → `56
    passed` (was 47 before this task). REFACTOR: none needed.
  - Verification (web/): `npm run lint` → clean, no output; `npm run
    typecheck` → clean, no output; `npm test -- --run` → `Test Files 12
    passed (12)`, `Tests 56 passed (56)`; `npm run build` → `tsc -b && vite
    build` succeeded, `dist/assets/index-Bt3SW5-1.js 481.18 kB │ gzip:
    152.87 kB`; `npm run size` → `Size: 151.66 kB gzipped` under the `200
    kB` limit.
  - Commit: `1e85546` — `fix(web): resolve review round 11 findings`.
    Authored lines (`git diff --stat` for this commit's 9 files, 8 modified
    + 1 new `errorCopy.test.ts`): 189 insertions, 30 deletions — above the
    ~150 forecast, mainly the four new/expanded test files the task
    required (stale-token, timeout/signal, retry, `describeApiError`
    coverage); not trimmed to fit the heuristic, per the ponytail/ODD
    instruction to never omit tests for a line-count budget.
  - #21 round 11 resolved in `1e85546`: all four WARNINGs, plus the
    `gen:api` and stale-comment suggestions. Not resolved (see Decisions
    above, both are the round's own "skip unless trivial" carve-out): the
    dead `if (error)` branches, the PWA old-bundle 404.
  - RDD assessment/acknowledgement for this commit not run by this writer —
    left to the parent orchestrator; boundary not advanced here (still
    `0547131`).

- 2026-09-24: T8 done by a delegated `sonnet-high` writer on branch
  `feat/e3-farms-web` @ `1e85546` (T7c done). Two work-unit commits, split
  by the task's own suggestion (pure geometry + map, then forms).

  **Commit 1: `feat(web): add plot polygon drawing map`.**
  `web/src/features/plots/polygon.ts` (new, pure): `Vertex`,
  `MIN_POLYGON_VERTICES = 3`, `buildPolygonGeoJson` — closes the ring
  (repeats the first vertex), emits `[lon, lat]` position order (matching
  `server/src/techcamp/farms/adapters/geojson.py`'s `x, y` = lon, lat and
  the server's `_rings_are_closed` ≥ 4 positions check), throws below the
  minimum. `web/src/features/plots/components/PlotDrawMap.tsx` (new):
  plain Leaflet (no draw plugin — ponytail, verified against
  `/leaflet/leaflet` via `find-docs`/ctx7: a `map.on('click', …)` handler
  plus `L.polygon`/`L.marker` is the whole interaction), click-to-add-vertex,
  `React.lazy`-loaded by its caller so Leaflet stays out of the main bundle.
  Added `leaflet` + `@types/leaflet` deps.
  - Decisions:
    - Tile source: docs/07-frontend-design-system.md (only names "teselas
      del mapa… caché en tiempo de ejecución") and ADR-0021 (seminar
      profile: emulated ports table) are both silent on which tile
      provider to use — **doc gap**, not invented as a new ADR per this
      task's instruction. Chose OpenStreetMap's public tile server
      (`tile.openstreetmap.org`): no API key/account, and it's the
      baseline example in Leaflet's own docs (confirmed via ctx7). Flagged
      for the owner to confirm or replace before any real deployment
      (OSM's tile usage policy disallows heavy production traffic without
      their permission).
    - Default map center/zoom: `[10.4, -73.25]`, zoom 8 — roughly Cesar/La
      Guajira/Magdalena (`AGENTS.md`'s "Caribe colombiano"), a starting
      view only; not a requirement, no doc names one.
    - No dedicated `PlotDrawMap.test.tsx`: the task explicitly allows
      mocking the Leaflet map in component tests: its own logic (event
      wiring, layer add/remove) is thin glue around the Leaflet API,
      covered indirectly by `CreatePlotSheet.test.tsx`'s mock and by a
      live smoke call through a real browser-less proxy request (see
      Verification) — a real-Leaflet-in-jsdom test would be brittle
      (layout measurement) for what it would prove.
  - TDD: mode on, source `AGENTS.md`/owner decision 2026-09-22, runner
    `npm test -- --run` (web/). RED observed per file before it existed:
    `polygon.test.ts` → `Failed to resolve import "./polygon"` (Vite
    import-analysis). GREEN after `polygon.ts`: `4 passed`. `PlotDrawMap.tsx`
    has no dedicated test (see Decisions above), so no separate RED/GREEN
    for it; its wiring is proved by commit 2's tests instead.
  - Verification (web/): `npm run lint` → clean; `npm run typecheck` →
    clean; `npm test -- --run` → `13 files, 60 passed` (up from 56);
    `npm run build` → succeeded, `PlotDrawMap.tsx` unreferenced yet so not
    in the graph, main bundle unchanged (152.87 kB gzip); `npm run size` →
    `151.66 kB` (limit 200 kB, unaffected — the module isn't imported
    anywhere until commit 2).
  - Commit: `3ede1ad` — `feat(web): add plot polygon drawing map`. Authored
    lines (`git diff --stat` for this commit's files, excluding
    `web/package-lock.json`): 158 insertions across 4 files (3 new, 1
    modified) — under the ~350 forecast for the whole task.

  **Commit 2: `feat(web): add farm and plot creation forms`.**
  `web/src/features/plots/api/plotsApi.ts`: `useCreateFarm(orgId)` and
  `useCreatePlot(farmId)` (`useMutation` + `queryClient.invalidateQueries`
  on success — verified against `/tanstack/query` via ctx7), `CreateFarmInput`/
  `CreatePlotInput` types. `web/src/features/plots/containers/
  CreateFarmSheet.tsx` (new): `POST /farms` — name, `municipality_code`,
  latitude/longitude number inputs building a GeoJSON `Point`; no
  `technician_id` field (optional on the API, out of this task's named
  scope). `CreatePlotSheet.tsx` (new): `POST /farms/{farm_id}/plots` —
  name, the lazily-loaded `PlotDrawMap` plus Deshacer/Limpiar buttons and a
  vertex-count hint, an irrigation-system `Select` (rainfed included),
  efficiency/flow inputs shown only while irrigated (ADR-0023); switching
  to rainfed clears any typed efficiency/flow from local state. Submit
  disabled until a name and ≥3 vertices exist; `area_ha` is never part of
  the request body (server-derived, T1 decision). Both forms follow
  `SignInScreen`'s existing error pattern: a `422 ApiError` shows
  `detail ?? title` inline on the form (the server's own validation
  message — no new field-mapping infrastructure, since no endpoint
  populates `ProblemError.errors[]` yet); anything else goes through the
  existing `describeApiError`. `PlotsList.tsx`: new optional `onAddPlot`
  prop renders an "Agregar parcela" button per farm section.
  `PlotsScreen.tsx`: a header "Nueva finca" button, the empty-state's
  action button, and the two sheets wired to local `useState`.
  - Decisions:
    - `web/package.json`'s `size-limit` config fixed: its glob
      (`dist/assets/*.js`) summed every JS asset, so the first lazy chunk
      this epic ever produced (`PlotDrawMap`'s Leaflet chunk, 43.87 kB
      gzip) pushed the reported number to 198.54 kB — technically still
      under 200 kB, but conflating an excluded lazy chunk with the
      "bundle inicial" docs/07 itself defines the budget as ("Bundle
      inicial ≤ 200 KB gzip… Mapa y gráficos se cargan de forma
      diferida" — same table row). Narrowed the glob to
      `dist/assets/index-*.js` (Vite's entry-chunk naming): after the
      fix, `npm run size` reports 155.1 kB, correctly excluding the
      lazy map chunk. A pre-existing gap this task's own lazy-loading
      requirement exposed, not a new rule.
    - 422 messages are shown as one form-level banner (server's `detail`,
      already multiple validation messages joined by `; ` in
      `parseErrorBody`), not mapped per input field: no endpoint sets
      `ProblemError.errors[]` (grepped `server/src/techcamp` — zero call
      sites), and FastAPI's own `RequestValidationError` shape has no
      stable per-field key the client could bind to a specific input
      without inventing one. "Shown sensibly" per this task's own wording
      is met by reusing the existing, already-established pattern
      (`SignInScreen`'s 401 handling) rather than adding new
      infrastructure for a case the backend doesn't yet support.
    - Farm location has no map picker: only plot boundaries got the
      polygon-drawing map (this task's explicit scope); a single point is
      two number inputs (ponytail: the smallest correct option, no second
      map interaction). `municipality_code` stays free text (T1's
      carried doc gap: no `municipality` table exists yet).
  - TDD: mode on, source `AGENTS.md`/owner decision 2026-09-22, runner
    `npm test -- --run` (web/). RED observed per new file before it
    existed (`Failed to resolve import` for `CreateFarmSheet`/
    `CreatePlotSheet`), then for the `PlotsScreen`/`PlotsList` wiring by
    `git stash push` of `PlotsScreen.tsx`/`PlotsList.tsx` only (keeping
    the two new wiring tests): `npm test -- --run
    src/features/plots/containers/PlotsScreen.test.tsx` → `2 failed, 7
    passed` (`Agregar parcela`/`Nueva finca` buttons not found). `git
    stash pop` restored the implementation: GREEN, same file → `9
    passed`. New tests: `CreateFarmSheet.test.tsx` (submits the GeoJSON
    point body, disables submit until required fields are filled, shows
    the 422 detail), `CreatePlotSheet.test.tsx` (submit disabled until a
    name and 3 vertices exist — via a mocked `PlotDrawMap`, rainfed hides
    efficiency/flow, an irrigated system shows them, submits the closed
    `[lon, lat]` polygon plus irrigation fields and closes the sheet, 422
    detail shown), plus two `PlotsScreen.test.tsx` wiring tests. REFACTOR:
    none needed.
  - Verification (web/): `npm run lint` → clean; `npm run typecheck` →
    clean; `npm test -- --run` → `15 files, 70 passed`; `npm run build` →
    succeeded, `PlotDrawMap` now split into its own chunk
    (`PlotDrawMap-*.js`, 149.72 kB / gzip 43.87 kB) separate from the main
    entry (`index-*.js`, 495.76 kB / gzip 156.34 kB); `npm run size` (after
    the config fix above) → `155.1 kB` gzipped, under the 200 kB limit.
  - Manual smoke check: real stack — `uv run alembic upgrade head` on the
    (previously unmigrated) dev Postgres, `uv run uvicorn
    techcamp.main:app` (seminar profile), `npm run dev` (Vite). Seeded one
    organization, user and owner membership directly in Postgres. Drove
    the OTP sign-in chain through the Vite proxy (`curl` against
    `localhost:5173`, not the UI itself), then the two new endpoints with
    the exact body shapes the forms send: `POST /api/v1/farms` with a
    GeoJSON `Point` location → `201` with the farm, no `area_ha` sent;
    `POST /api/v1/farms/{id}/plots` with a closed 4-position `[lon, lat]`
    polygon and `irrigation_system: "drip"` → `201` with a
    server-computed `area_ha`; a second plot with `irrigation_system:
    "none"` and null efficiency/flow → `201`, both fields null (ADR-0023);
    a 3-position (open) ring → `422` with the same FastAPI validation
    shape `client.ts` already parses. All five calls returned the
    expected status and body. Cleaned up the seeded rows, ran `uv run
    alembic downgrade base` to restore the dev database to its
    unmigrated state, and stopped both processes afterward.
  - Commit: `0f7fb5a` — `feat(web): add farm and plot creation forms`.
    Authored lines (`git diff --stat` for this commit's files, excluding
    `web/package-lock.json`): 701 insertions, 7 deletions across 9 files
    (4 new, 5 modified) — well above the task's ~350 forecast, same reason
    as most tasks in this epic: two complete forms (name/location fields,
    the map, an irrigation select with conditional fields, mutations,
    error handling) plus their tests don't split smaller within one
    coherent work unit without cutting tests. Flagging for the owner/
    parent orchestrator's delivery-strategy decision, not re-split here.
    RDD assessment/acknowledgement for both T8 commits not run by this
    writer — left to the parent orchestrator; boundary not advanced here
    (still `0547131`).
  - Doc gaps: (1) tile source (see Decisions above) — new. (2) carried,
    unchanged: `farm.municipality_code` plain `text`; `GET /me` has no
    organization name.

- 2026-09-24: T8b done by a delegated `sonnet-high` writer on branch
  `feat/e3-farms-web` @ `431d75b` (T7c + T8 reviewed, plus the owner's map-tile
  decision recorded). Resolves GitHub issue #21 round 12 (three WARNINGs, all
  four suggestions).
  - `web/src/lib/api/client.ts`: new `combineWithTimeout` helper —
    `AbortSignal.any` (Chrome 116+, Safari 17.4+) is feature-detected
    (`typeof AbortSignal.any === 'function'`) before use; when absent (older
    field WebViews), the timeout signal alone wins and a caller-initiated
    abort no longer cancels the request (accepted tradeoff, task instruction).
    `REQUEST_TIMEOUT_MS` exported so the test asserts the same constant.
  - `web/src/lib/api/client.test.ts`: replaced the vacuous timeout test
    (asserted only that *some* `AbortSignal` was attached, which can never
    fail) with `rejects with a TimeoutError once a stalled request passes
    REQUEST_TIMEOUT_MS` — fake timers plus a stubbed `AbortSignal.timeout`
    (real `AbortSignal.timeout` isn't driven by vitest's fake timers; a
    `setTimeout`-based stand-in is), a stalled `fetch` mock that only settles
    on its request signal aborting. New `falls back to a timeout-only signal
    when AbortSignal.any is unavailable`: deletes `AbortSignal.any` for the
    call, proves the request still completes instead of throwing.
  - `web/src/features/plots/api/plotsApi.test.tsx` (new): two `renderHook`
    tests (`useFarms`+`useCreateFarm`, `usePlotsByFarm`+`useCreatePlot`)
    proving each list goes from 0 to 1 item after its create mutation and
    that exactly 3 fetches happen (initial GET, POST, refetch GET) — proves a
    real invalidation-triggered refetch, not the mutation's own response
    being reused. The existing `['farms', orgId]`/`['plots', farmId]`
    invalidation keys already matched their query keys (verified by
    temporarily breaking each to a wrong key and observing the new test fail
    — see TDD below); no key fix was needed, only the missing coverage the
    review flagged.
  - `web/src/features/plots/components/PlotDrawMap.tsx`: `L.circleMarker`
    replaces `L.marker` for a single drawn vertex — the default Leaflet
    marker icon's relative asset paths break under Vite's bundling
    (find-docs/ctx7 confirmed against `leaflet/leaflet`'s `DefaultIcon.js`);
    `circleMarker` needs no icon asset at all (ponytail: smaller than
    importing/re-pointing the marker images). Added one deferred
    `map.invalidateSize()` (`setTimeout(..., 0)`, cleared on unmount) after
    mount: the sheet's own slide-in is a CSS `transform` (doesn't change the
    container's box size), but Leaflet's first tile fetch can still measure
    a not-yet-settled first paint inside an animating ancestor.
  - `web/src/features/plots/components/PlotDrawMap.test.tsx` (new): glue
    test with a mocked `leaflet` module (task instruction) — click calls
    `onMapClick` with `{lat, lng}`; `map.remove()` on unmount;
    `map.invalidateSize()` called after mount; a single vertex draws via
    `L.circleMarker`, not `L.marker`; the tile layer is added with an
    attribution string containing "OpenStreetMap".
  - OSM attribution (task item 5): verified, not changed — Leaflet's
    `L.Map` defaults `attributionControl: true`
    (`node_modules/leaflet/dist/leaflet-src.js:5865-5868`, grepped directly)
    and `PlotDrawMap.tsx` already passes `attribution: TILE_ATTRIBUTION` to
    `L.tileLayer`, so the OSM attribution control was already showing; no
    code change needed, only the new glue test's assertion.
  - `web/src/lib/api/errorCopy.test.ts`: the "generic copy... including an
    ApiError" case passed a plain `Error`, never an actual `ApiError`; now
    builds a real `ApiError(422, 'Validation error', 'name: Field required')`
    and keeps a separate plain-`Error` case. `describeApiError` needed no
    change (it doesn't special-case `ApiError`; both fall into the generic
    branch) — this was a mislabeled test, not a bug.
  - `web/src/features/plots/containers/CreateFarmSheet.tsx`: client-side
    latitude/longitude range check (−90..90, −180..180) folded into
    `canSubmit`, plus an inline hint shown once both fields are non-empty and
    out of range. Server-side validation (T2b) still owns the authoritative
    check; this only avoids a round trip for an obviously invalid pair.
  - `docs/adr/0021-perfil-seminario-local.md`: new "Teselas del mapa" table
    row plus a decision paragraph — OpenStreetMap public tiles in **every**
    profile (not seminar-only, unlike the rest of the table), owner decision
    2026-09-24; the map base needs internet to show, but drawing/saving the
    polygon doesn't (only the final `POST` touches the network); the OSM
    attribution requirement is carried into code (`PlotDrawMap.tsx`,
    verified above).
  - Decisions: no new decisions beyond the ones inline above
    (`combineWithTimeout`'s fallback semantics, `circleMarker` over fixing
    marker icon assets, ADR tile scope covering both profiles).
  - TDD: mode on, source `AGENTS.md`/owner decision 2026-09-22, runner
    `npm test -- --run` (web/). RED observed per behavior, by temporarily
    reverting only the relevant implementation file(s) with `git stash push`
    and running the new/changed test file:
    - `client.ts` reverted → `client.test.ts`: the new timeout test **timed
      out** (5000ms, the real `AbortSignal.timeout` never fires under fake
      timers) and the new fallback test failed `TypeError: AbortSignal.any is
      not a function`. Restored: GREEN, `13 passed`.
    - `PlotDrawMap.tsx` reverted → `PlotDrawMap.test.tsx`: `invalidates the
      map size after mount` failed (`invalidateSize` called 0 times) and
      `draws a circleMarker...` failed `TypeError: default.marker is not a
      function` (mock has no `marker`). Restored: GREEN, `5 passed`.
    - `CreateFarmSheet.tsx` reverted → `CreateFarmSheet.test.tsx`: the new
      range-check test failed (`toBeDisabled()` on an enabled button).
      Restored: GREEN, `4 passed`.
    - `plotsApi.ts`'s two invalidation keys, each temporarily changed to a
      wrong `queryKey` one at a time: the matching new test failed (`data`
      stayed length 0). Reverted to the real keys: GREEN, `2 passed`.
    - `errorCopy.test.ts` and the ADR row needed no RED (no implementation
      bug — pure coverage/doc additions).
  - Verification (web/): `npm run lint` → clean; `npm run typecheck` →
    clean; `npm test -- --run` → `17 files, 80 passed` (up from 70); `npm run
    build` → succeeded (`PlotDrawMap-*.js` 149.79 kB / gzip 43.89 kB still its
    own chunk, `index-*.js` 496.07 kB / gzip 156.47 kB); `npm run size` →
    `155.22 kB` gzipped (limit 200 kB).
  - Commit: `4c1069b` — `fix(web): resolve review round 12 findings`. Refs
    #21. Authored lines (`git diff --stat --cached -- . ':!web/package-lock
    .json'` for this commit's files): 340 insertions, 12 deletions across 9
    files (2 new, 7 modified) — above the ~150 forecast, consistent with
    every other fix task in this epic (three WARNINGs plus four suggestions,
    each needing its own new test, plus a docs row). One commit: all nine
    files serve the same round-12 fix, and the ADR row directly documents the
    same `PlotDrawMap` tile decision the code changes touch (`work-unit-
    commits` skill: "docs belong with the feature... they explain";
    consistent with this epic's own T5 precedent of folding an ADR row into
    the feature commit rather than a separate `docs(adr)` commit). RDD
    assessment/acknowledgement not run by this writer — left to the parent
    orchestrator; boundary not advanced here (still `7749c8f`).
  - Skipped/open items: self-intersection polygon validation (deferred since
    T2b, unrelated to round 12); `VITE_API_URL` dual-use note already closed
    in T7c; no new doc gaps beyond the ones carried forward.

- 2026-09-24: T9 done by a delegated `sonnet-high` writer on branch
  `feat/e3-farms-web` @ `4c1069b` (T8b done). Two work-unit commits: the round
  13 test-hardening fix, then the T9 feature.

  **Commit 1: `test(web): harden timeout tests`.** Resolves GitHub issue #21
  round 13 (both items from the T8b review). `web/src/lib/api/client.test.ts`:
  the fake-timer timeout test's `timeoutSpy.mockRestore()`/`vi.useRealTimers()`
  cleanup moved into `finally`, so a failed assertion can't leak fake timers
  into later tests (WARNING). The `AbortSignal.any`-unavailable fallback test
  now spies on `AbortSignal.timeout` and asserts it was called with
  `REQUEST_TIMEOUT_MS`, proving the timeout-only path itself instead of only
  that the request didn't throw (SUGGESTION).
  - TDD: mode on, source `AGENTS.md`/owner decision 2026-09-22, runner `npm
    test -- --run` (web/). No production behavior changed by this commit, so
    RED was observed by mutation testing instead of a pre-existing bug: with
    `client.ts`'s `combineWithTimeout` temporarily changed to drop the
    timeout signal in its fallback branch (returning the caller's signal
    unchanged), the new fallback test failed —
    `AssertionError: expected "timeout" to be called with arguments: [ 10000
    ]`, `Number of calls: 0`. Reverted the probe: GREEN, `npm test -- --run
    src/lib/api/client.test.ts` → `13 passed`.
  - Verification (web/): `npm run lint` → clean; `npm run typecheck` →
    clean; `npm test -- --run` → `17 files, 80 passed`; `npm run build` →
    succeeded; `npm run size` → unaffected (test-only change).
  - Commit: `a04e0a1` — `test(web): harden timeout tests`. Authored lines
    (`git diff --stat` for this commit's one file): 25 insertions, 15
    deletions. #21 round 13 resolved in `a04e0a1`.

  **Commit 2: `feat(web): add plot soil and crop cycle forms with Kc`.**
  New `web/src/features/plots/containers/PlotDetailSheet.tsx`: a plot-detail
  sheet (docs/07 mapa de pantallas: `plots --> plotd[Parcela: polígono,
  suelo, ciclo]`) with two sections built from existing primitives only
  (`Sheet`, `Select`, `Input`, `Button`, no new design-system code, design
  frozen per the epic's owner decision):
  - **Soil**: `Autocompletar desde SoilGrids` (`POST
    /plots/{id}/soil:autofill`) with a dedicated "SoilGrids no está
    disponible en este momento. Intenta de nuevo más tarde." message for a
    502/503 upstream failure, and `Editar manualmente` opens a full-document
    `PUT /plots/{id}/soil` form for every field docs/04-api.md's
    `SoilProfilePutRequest` defines. The FAO-56 Table 19 texture fallback
    stays server-side (T4); `texture` is a fixed `Select` built from the
    nine keys `FAO56_TEXTURE_WATER_LIMITS` (server, `domain/models.py`)
    actually recognizes, not free text — a Spanish-speaking user typing
    "arcilla" instead of `clay` would otherwise silently miss the fallback
    (`source` stays `null`).
  - **Crop cycle**: a start-cycle form (`POST /plots/{id}/cycles`) with a
    crop `Select` from `GET /crops` showing that crop's Kc by stage and
    `kc_source` once selected, a sowing date, and an *optional* expected-
    harvest override. `POST`'s own request body only ever carries `{crop_id,
    sown_on}` (docs/04-api.md:56, `CropCycleCreateRequest` has no
    `expected_harvest_on` field — T6 decision: the server derives it from
    the crop's stage lengths) — when the override field is filled, a
    follow-up `PATCH /cycles/{id} { expected_harvest_on }` applies it, and
    the client rejects (disables submit, shows a message) an override
    earlier than the sowing date before either call runs. An active cycle
    shows crop/dates/status with `Registrar cosecha`/`Registrar pérdida`
    (`PATCH { status }`). 409 (`ActiveCropCycleExistsError`) and 422 show the
    server's own title/detail inline, same pattern as `err.status === 422`
    in `CreateFarmSheet`/`CreatePlotSheet`.
  - **Entry point**: `PlotsList.tsx`'s per-plot `<li>` (previously inert
    text) is now a full-width `<button>` calling a new `onSelectPlot` prop;
    `PlotsScreen.tsx` wires it to a `selectedPlot` state and renders
    `PlotDetailSheet`, the same open/close pattern `creatingPlotForFarmId`
    already established for `CreatePlotSheet`.
  - `plotsApi.ts` additions: `useSoilProfile`/`useActiveCycle` (queries with
    `enabled: false`, `useCrops` (`GET /crops`), `usePutSoil`/
    `useAutofillSoil`/`useCreateCycle`/`usePatchCycle` (mutations that
    `setQueryData` the plot-scoped cache key on success).
  - Decisions:
    - **No GET endpoint for a plot's soil profile or active cycle**:
      docs/04-api.md:43-56 lists only `PUT`/`POST .../soil:autofill` for
      soil (T4's own carried decision) and only `POST`/`PATCH` for cycles —
      no read endpoint for either exists to refetch from. `useSoilProfile`/
      `useActiveCycle` are TanStack Query reads with `queryFn` that reject
      and `enabled: false` (never actually fetch); every write mutation
      (`usePutSoil`, `useAutofillSoil`, `useCreateCycle`, `usePatchCycle`)
      calls `queryClient.setQueryData` on success so the sheet can display
      the result of whatever it just did. This persists across closing and
      reopening the sheet within the same app session (the `QueryClient` is
      a module singleton), but a genuinely fresh session (page reload) shows
      no soil/cycle data until the user acts again — a real, disclosed gap
      the docs already have (not invented or worked around here, e.g. no
      speculative `localStorage` cache).
    - Texture `Select` values/keys (`sand`, `loamy_sand`, `sandy_loam`,
      `loam`, `silt_loam`, `silt`, `silty_clay_loam`, `silty_clay`, `clay`)
      are copied from the server's own `FAO56_TEXTURE_WATER_LIMITS` dict
      keys (`server/src/techcamp/farms/domain/models.py`), with Spanish
      labels — this is a label list, not the FAO-56 math itself (which the
      task explicitly keeps server-side); flagged as a coupling to watch if
      that dict's keys ever change.
    - `Select`'s options loading asynchronously (the crop catalog, unlike
      every prior Select in this epic, whose options are static arrays)
      surfaced a pre-existing jsdom gap: `Element.scrollIntoView` doesn't
      exist in jsdom, and Radix's `Select` calls it when it highlights the
      candidate item on open. Stubbed once in `src/test/setup.ts` (global,
      benefits every Select-based test, not just this feature's).
    - No dedicated screen route for plot detail: docs/07 names the
      navigation node (`plots --> plotd`) but not its layout; a sheet
      reuses the same composition pattern `CreateFarmSheet`/`CreatePlotSheet`
      already established instead of adding routing/a new screen shell
      (ponytail, ADR-0006/docs/07 frozen-design instruction).
  - TDD: mode on, source `AGENTS.md`/owner decision 2026-09-22, runner `npm
    test -- --run` (web/). RED observed by moving the new
    `PlotDetailSheet.tsx` aside and `git stash push` of the three modified
    implementation files (`plotsApi.ts`, `PlotsList.tsx`, `PlotsScreen.tsx`),
    keeping every new/changed test: `npm test -- --run` → `3 failed | 15
    passed (18)` — `PlotDetailSheet.test.tsx` failed to collect (`Failed to
    resolve import "./PlotDetailSheet"`), `plotsApi.test.tsx`'s two new
    caching tests failed (`useSoilProfile`/`useActiveCycle`/etc. not
    exported), `PlotsScreen.test.tsx`'s new entry-point test failed (no
    `button` with an accessible name matching the plot row). Restored the
    implementation: GREEN, `npm test -- --run` → `18 files, 93 passed`.
    REFACTOR: none needed beyond what was written directly.
  - Verification (web/): `npm run lint` → clean; `npm run typecheck` →
    clean; `npm test -- --run` → `Test Files 18 passed (18)`, `Tests 93
    passed (93)`; `npm run build` → succeeded (`index-*.js` 506.46 kB / gzip
    158.80 kB, `PlotDrawMap-*.js` still its own lazy chunk); `npm run size` →
    `157.54 kB` gzipped (limit 200 kB, up from T8b's 155.22 kB).
  - Manual smoke check: real stack — `uv run alembic upgrade head` on the
    (previously unmigrated) dev Postgres, `TECHCAMP_PROFILE=seminar uv run
    uvicorn techcamp.main:app`, `npm run dev`. Seeded one organization, user,
    owner membership, farm and a rainfed plot directly in Postgres. Drove
    the OTP sign-in chain, then every T9 endpoint through the Vite proxy
    (`curl` against `localhost:5173`, not the UI itself): `PUT
    /api/v1/plots/{id}/soil` with the manual-edit body shape → `200`,
    `source: "lab"`; `POST /api/v1/plots/{id}/soil:autofill` → `200`,
    `source: "soilgrids"` (the live ISRIC call succeeded from this
    environment); `GET /api/v1/crops` → `200`, stages/Kc/`kc_source`
    present; `POST /api/v1/plots/{id}/cycles {crop_id: 1, sown_on:
    "2026-06-01"}` → `201`, `expected_harvest_on` server-derived
    (`2026-08-30`); a second `POST .../cycles` on the same plot → `409`,
    `title: "Plot already has an active crop cycle"` (confirms the client's
    `err.detail ?? err.title` fallback is exercised for real — this
    `ProblemError` carries no `detail`); `PATCH /api/v1/cycles/{id}
    {status: "harvested"}` → `200`, `status: "harvested"`. All six calls
    matched the shapes `plotsApi.ts`/`PlotDetailSheet.tsx` send and parse.
    Cleaned up the seeded rows, ran `uv run alembic downgrade base` to
    restore the dev database to the unmigrated state it was found in, and
    stopped both processes afterward.
  - Commit: `31690d2` — `feat(web): add plot soil and crop cycle forms with
    Kc`. Authored lines (`git diff --stat` for this commit's files): 1059
    insertions, 8 deletions across 8 files (2 new, 6 modified) — well above
    the ~300 forecast, same reason as almost every task in this epic: two
    complete forms (soil autofill/edit, crop-cycle start/end) plus a new
    entry point and full test coverage (data-layer caching tests, sheet
    interaction tests, an entry-point wiring test) don't split smaller
    within one coherent work unit without cutting tests. Flagging for the
    owner/parent orchestrator's delivery-strategy decision, not re-split
    here. RDD assessment/acknowledgement not run by this writer (task
    instruction: do not run `gentle-ai review` commands) — left to the
    parent orchestrator; boundary stays `7749c8f`.
  - Doc gaps: (1) new — no `GET` endpoint for a plot's soil profile or
    active cycle (see Decisions above); the plot-detail screen's layout
    itself is also unspecified beyond docs/07's navigation node. (2)
    carried, unchanged from T1-T8b: `farm.municipality_code` plain `text`;
    `GET /me` has no organization name; OSM public tiles (ADR-0021, T8b).
  - Skipped/open items: cross-org 404 for soil/cycles and rainfed
    efficiency/flow rejection are server-scope acceptance criteria already
    provable before this task (T2/T2b/T3b/T4/T6), untouched here. This
    writer did not run `gentle-ai review` or edit the GitHub issue, per the
    task instructions.

## Next step
E3 PR slicing (stacked-to-main).
