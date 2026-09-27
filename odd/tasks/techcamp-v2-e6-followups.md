# TechCamp v2 — E6 Review Follow-ups

## Objective
Resolve the open non-blocking RDD findings of epic E6 (irrigation), tracked in issues #94, #96, #97, #99, #100, #101, #102 (labels `review-follow-up`, `epic:e6`), and close each issue with `Refs #N` commits and a `Closes #N` PR.

## Problem
E6 shipped to `main` (`b627b66`, PRs #104–#111) with WARNING/SUGGESTION findings deferred to the tracker (AGENTS.md Workflow). Several are real defects: assimilated `Dr` is not clamped to 0..TAW, the sensor daily mean uses a UTC-midnight window instead of the Bogota local day and counts out-of-range readings, a missing forecast silently becomes 0 mm, an empty stage list crashes the job, the water-balance range cap accepts 367 days, and many branches are untested.

## Why
E7 (alerts, `water_stress`) and E9 (plot status) consume `water_balance_daily` and `irrigation_recommendation`. Fixing the balance and its contracts before they are built on keeps the defects from spreading.

## Scope
- In: every still-open finding in #94, #96, #97, #99, #100, #101, #102, plus the two "Orchestrator docs check" items of #99.
- In: doc-only commit `495659f` (E6 delivery record), cherry-picked as `d0d1728`.
- In (owner, 2026-09-27): #103 (Bogota local-day helper duplication, E5 weather code) as the last task.
- Out: #89 (CI teardown flake), irrigation push wiring (waits for E7's outbox on `main`).
- Already fixed on `main` (verified 2026-09-26 on `d0d1728`; noted on the issue when it closes):
  - #96 R3-repo-commits-non-atomic: both `upsert` methods flush and the caller commits once.
  - #97 R3-kc-none-string-check and the "application imports AsyncSession" docs check: `run_daily_balance.py:220` compares `== KcSource.NONE` only, no session import.
  - #99 R3-caller-contract: the `run_plot_balance` job commits, proven by `test_per_plot_task_persists_balance_and_recommendation_and_commits`.
  - #101 R3-fallback-day-utc: removed in quality pass Q2.
  - #102 R3-kc-source-identity-check: fixed in Q3.

## Owner docs
- docs/06 §5 (FAO-56 balance, `K` table, representative sensor, run temporal semantics, incomplete soil) and §6 (weather degradation, `low_confidence`).
- docs/04 "Riego" (recommendation and water-balance endpoints, 366-day cap, Bogota defaults) and "Estado de la parcela" (`status` values).
- docs/03 `water_balance_daily`, `plot` e `irrigation_recommendation`, Calibración.
- ADR-0009 (FAO-56), ADR-0022 (stress `Dr > RAW`, weighted assimilation), ADR-0023 (irrigated vs rainfed), docs/09 Seguridad (org isolation per repository).

## Constraints
- AGENTS.md: docs win; hexagonal layers + import-linter (`application` depends only on `domain`); every repository filters by `org_id` (docs/09); English code and artifacts.
- Ponytail: shortest correct diff, no new dependencies.
- A fix that changes documented behavior updates the owning doc in the same commit.

## Decisions (approved by the owner 2026-09-27)
- D1 #97 missing forecast: do not skip the plot; flag it in the `rationale` (`forecast_missing`), mirroring `low_confidence`. Document in docs/06 §5 and the `rationale` list of docs/04 Riego.
- D2 #99 freshness anchor: the "valid reading within 24 h" check and the calibration lookup anchor to the end of local day D−1 (the balance day), not to the run time, so a rerun or backfill of the same day is deterministic. Clarify in docs/06 §5.
- D3 #99 window and validity: irrigation computes the sensor daily mean from raw readings over the America/Bogota local day D−1, excluding readings flagged out of range (`reading.quality` bit 2, docs/03 Calibración / E4 decision). The shared `reading_daily` aggregate and telemetry read API stay unchanged. Clarify "lectura válida" in docs/06 §5.
- D4 #101 range cap: count calendar days inclusive of both ends, like the 30-day default window (docs/04 Riego); reject when the inclusive count exceeds 366 (`(to − from).days > 365`). Fix the mislabeled test.
- D5 #94 R3-003 / #102 R3-status-zero-raw-edge: `RAW <= 0` (degenerate soil) has no positive threshold, so status `ok`, rainfed advice `no_action`, irrigated kind `not_needed` (no endless `postpone`). Documented in docs/06 §5 and docs/04 Riego (T1).

## Route and checks
- TDD: on (owner decision 2026-09-22, AGENTS.md "Testing"). RED → GREEN → REFACTOR per issue, observed with targeted runs only (`uv run pytest tests/irrigation/test_x.py::test_name` or the issue's test file). RED must fail on an assertion or a missing name, not only a missing module. Runner: `uv run pytest` in `server/`.
- Fast checks per commit: targeted tests of touched modules, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`.
- Full `uv run pytest` runs once, at the end of implementation, before delivery; on failure only the failing tests are re-run.
- Parent gate per commit: on the committed SHA in detached worktree `e6f-verify`, DB `techcamp_verify` — targeted module tests (`tests/irrigation`, plus `tests/farms`/`tests/telemetry`/`tests/weather` when touched), ruff, format, mypy, lint-imports, and a diff-vs-owner-doc check. Quality issues found here are fixed immediately by the same writer session.
- Route (owner, 2026-09-27): T1–T3 (+ #115, #116) by a Claude Code `odd-worker` subagent (Sonnet) inside the orchestrator session; T4–T7 by AGY in one Herdr session (brief `.git-brief-t4-t7.md`), the parent runs RDD on AGY's commits as `claude-code`. Worker rule: one worker continued across all tasks via SendMessage (owner: at most 3 subagents per session). `DATABASE_URL=postgresql+asyncpg://techcamp:techcamp@localhost:5438/techcamp`. Test runs (writer and parent) go in the background.
- Every brief: Step 0 CodeGraph first (`codegraph status`, then `codegraph_explore` / read-only CLI); docs to read; items; TDD rule; checks; commit message; stage only own files; no AI attribution; do not push. Briefs live in the worktree as `.git-brief-*.md` (excluded via `info/exclude`).
- Units over ~300 authored lines split into a/b sub-units, one commit each.

## Review (RDD)
- On (global). With the in-session worker, the parent runs RDD as `claude-code` after each work-unit commit: `gentle-ai review assess --cwd <worktree> --agent claude-code --base-ref <last reviewed boundary> --committed-only --json` per work-unit commit on a clean worktree and follows `next_transition` verbatim. First boundary: branch point `b627b66` (the doc commit `d0d1728` is passive).
- Consent: fixes, not features — the standing feature grant does not apply. Owner, 2026-09-27: for this session only, consent on fix candidates is `granted` by default; the parent runs the envelope's exact `granted` invocation and records it.
- Before acknowledging, the parent records every finding's `.claim` from `reviewer_results`.
- Blocking findings: fixed in the bounded correction. Non-blocking findings of this feature: one issue per review round (`review-follow-up`, `epic:e6`, `area:server`, `type:*`), fixed right away by a worker resumed with the same context (`Refs #N`), then reviewed again until clean.
- `under_budget` stays pending in the slice and is recorded.
- Review rounds opened: #115 (T1+T2), #116 (T3), #117 (#116 fix + T5–T7), #118 (T4), #119 (#118 fix), #120 (#119 fix), #121 (left open by owner decision).

## Tasks
Forecast ≈ 1,300 authored lines (tests included).

- [x] T1 Domain rules — #94 (R3-001..005) + #102 (R3-status-zero-raw-edge, R3-local-today-untested, R3-router-enum-fallback-removed-unproved) — grouped because #94 R3-003 and #102 R3-status-zero-raw-edge touch the same function (`compute_water_balance_status`). Files: `irrigation/domain/models.py`, `tests/irrigation/test_domain_models.py`, `tests/irrigation/test_api.py` (enum contract test). ≈ 250 lines.
  - Clamp assimilated/observed `Dr` to 0..TAW (docs/06 §5 "Dr modelado" range); unit-suffix `is_sensor_depth_representative` params (cm) with a test; guard `RAW <= 0` (D5); boundary tests at `Dr == RAW`, `Dr == 0.8·RAW`, efficiency fallback, `duration_min = None`, `stage_for_cycle_day` ValueError, empty stages; stage-name mismatch fails loudly; `local_today` early-UTC and naive-input tests; API test pinning enum members.
  - Done: `f711726` (399 authored, incl. docs/06 §5 + docs/04 for the clamp and D5), odd-worker. RED: `TypeError: compute_observed_depletion() missing 1 required positional argument: 'taw_mm'` (+ `assimilate_depletion`, `is_sensor_depth_representative(root_depth=...)`); D5 and R3-005 pinned test-first; R3-004 and `local_today` tests are characterization (already correct). #102 enum contract already pinned by `test_get_recommendation_rainfed_for_day`. Stage names are mirrored in `_KNOWN_STAGE_NAMES` because `domain` cannot import `farms`. Parent gate on `f711726` (e6f-verify, techcamp_verify): tests/irrigation 97 passed; ruff, format, mypy (129 files), lint-imports green; docs diff matches D5. RDD: medium, `under_budget`, then reviewed with T2 (below).
- [x] T2 Persistence — #96 (upsert-returning-stale, balance-get-org-filter-untested, migration-cycle-untested, advice-silent-drop, upsert-ignores-model-plot-day). Files: `irrigation/adapters/repositories.py`, `tests/irrigation/test_repositories.py`. ≈ 150 lines.
  - Done: `ecd93ed` (143 authored), odd-worker (same session). RED: `Failed: DID NOT RAISE ValueError` (plot/day mismatch). Upsert staleness could not be reproduced; `populate_existing=True` kept as SQLAlchemy's documented ORM-upsert-RETURNING practice and the overwrite's return value is now asserted. Migration cycle: false docstring fixed (conftest `_migrated_schema` upgrades to head and downgrades). `migrations/env.py` `fileConfig(..., disable_existing_loggers=False)` so `caplog` sees app loggers. Parent gate on `ecd93ed`: tests/irrigation 99 passed; ruff, format, mypy, lint-imports green.
  - RDD T1+T2 (`d80c7ac..ecd93ed`, medium, `slice_budget_reached`, consent granted by owner): lineage `review-da21b434666149d3`, review-reliability, approved, acknowledged (burned). 1 WARNING + 2 SUGGESTION → #115, fixed now by the same session. Boundary → `ecd93ed`.
- [x] T3 Daily balance use case — #97 (missing-forecast D1, low-confidence-untested incl. 24 h boundary, skip-branches-untested, atomic-write-unproved, empty-stages-indexerror). Files: `irrigation/application/run_daily_balance.py`, `tests/irrigation/test_run_daily_balance.py`, docs/06 §5, docs/04 Riego. ≈ 300 lines (split a: forecast + low-confidence + empty stages; b: skip branches + atomicity, if over budget).
  - Done (worker 1): #115 fixes `da7b31a` (148; new skip `crop_stage_invalid`, app-level clamp test, RAW ≤ 0 rainfed/negative tests; RED: uncaught `ValueError: unknown crop stage name(s): ['inicial']`). T3a `061d04a` (272; `forecast_missing` flag, D−1 ET0 falls back to the other D−1 row, else skip `missing_et0_for_balance_day`; low-confidence 24 h boundary/tz tests; empty stages → `crop_stage_invalid`; docs/06 §5 + docs/04 rationale list; RED: `assert False` on `result.skipped` with the ET0 guard reverted). T3b `4853fc4` (200; one test per skip reason with no rows written; job-level atomicity via a raising recommendations repo in `run_plot_balance`; characterization). Parent gate on `4853fc4`: tests/irrigation 117 passed; ruff, format, mypy, lint-imports green; docs diff matches D1.
  - RDD `ecd93ed..4853fc4` (medium, `slice_budget_reached`, 620 lines, consent granted by session default): lineage `review-7e96fa07c24925ab`, review-reliability, approved, acknowledged (burned). 1 WARNING + 2 SUGGESTION → #116, fixed now by worker 1. Boundary → `4853fc4`.
  - #116 fix `2e7b04e` (213; `InvalidCropStagesError(ValueError)` narrows the catch to stage-data problems; ET0-from-forecast sets `missing_observed_weather`, which docs/06 §5 already covers; partial-window `forecast_missing` tests; RED: `assert False is True` on `missing_observed_weather`). Parent gate: tests/irrigation 121 passed; ruff, format, mypy, lint-imports green. RDD: medium, `under_budget` — pending in the slice, reviewed with T4.
- [x] T4 Sensor assimilation — #99 (freshness anchor D2, local-day window + quality D3, daily-value-none, untested branches, weak assimilation assertion). Files: `run_daily_balance.py`, telemetry read port only if needed for raw readings with quality, tests, docs/06 §5. ≈ 250 lines.
- [x] T5 Daily job — #100 (weather repo import from `weather.adapters.repositories`, fan-out test isolation, IntegrityError narrowed to the queueing-lock constraint). Files: `irrigation/adapters/jobs.py`, `tests/irrigation/test_jobs.py`. ≈ 80 lines.
- [x] T6 Read API — #101 (tz default tests with an 00:00–05:00 UTC instant, range cap D4, `dependency_overrides.pop(get_now, None)`, 422 problem+json shape). Files: `irrigation/application/query_irrigation.py`, `tests/irrigation/test_api.py`, docs/04 Riego if wording needs it. ≈ 100 lines.
- [x] T7 Local-day helper — #103: one pure helper in `shared/` (stdlib `zoneinfo`) used by weather and irrigation. ≈ 60 lines.
  - Done in parallel (AGY, Herdr, own worktrees/DBs; ODD check: ok for all three):
    - T5 `1adf98a` on `fix/e6f-t5` (125; weather repo import from `weather.adapters.repositories`; fan-out counts scoped to the test's own plots; `_defer_plot_job` re-raises any IntegrityError that is not an asyncpg `UniqueViolationError` on `procrastinate_jobs_queueing_lock_idx_v1`; RED `DID NOT RAISE IntegrityError`). Parent quality fix: typed check instead of a getattr chain with a dead string fallback (amend).
    - T6 `6c89e5e` on `fix/e6f-t6` (24; D4 `(to − from).days > 365`, tests 366 accepted / 367 rejected; default-day tests at 03:00 UTC; `dependency_overrides.pop(get_now, None)`; 422 problem+json + title; docs/04 "contando ambos extremos").
    - T7 `c59bceb` on `fix/e6f-t7` (165; `techcamp.shared.dates` with `BOGOTA_TZ` + `local_today`, used by irrigation, weather jobs/dev jobs and query_weather; tests in `tests/shared/test_dates.py`). Parent quality fix: no compatibility re-exports, no `_LOCAL` alias, one test home (amend).
  - Chain in `e6f-verify`: `2e7b04e` + T5 `baeb616` + T6 `45271ee` + T7 `f43880a` (one trivial import conflict in `tests/irrigation/test_jobs.py`, resolved by the parent). Parent gate: tests/irrigation+weather+shared 217 passed; ruff, format, mypy (130 files), lint-imports green.
  - RDD `4853fc4..f43880a` (#116 + T5 + T6 + T7; medium, `slice_budget_reached`, 19 files / 512 lines; consent granted by session default): lineage `review-661fab4225ab6d4f`, review-reliability, approved, acknowledged (burned). 1 WARNING (T5 test) + 1 SUGGESTION (T7 test) → #117, fixed now by the owning AGY sessions. Boundary → `f43880a` (chain).
  - T4 (AGY, `e6-followups`; ODD check: ok): first commit 756 lines with dead NaN/None checks; parent asked for one NaN filter, a shared test helper and an a/b split. Rebased onto the chain by the parent; the stale `BOGOTA_TZ` import fixed by the writer via fixup. T4a `d9c1b62` (366; `query_valid_raw` on the telemetry port, org-scoped, excludes `quality & 2`; Bogota local-day window D−1 converted to UTC; calibration and 24 h check anchored to the end of that day; docs/06 §5 "lectura válida"; RED `AttributeError: ... no attribute 'query_valid_raw'`, `assert 0.0 == 42.0 ± 0.1` for D2/D3). T4b `49fb2ee` (85; branch tests and exact assimilated depletion).
  - #117 fixes cherry-picked onto the chain: `a058962` (T5 test chains a real `UniqueViolationError` with another constraint name) and `5b0710b` (T7 default-clock test bounded by before/after reads).
  - Parent gate on `5b0710b`: tests/irrigation+weather+shared+telemetry reading repo 229 passed; ruff, format, mypy, lint-imports green; docs/06 diff matches D2/D3.
  - RDD `f43880a..5b0710b` (medium, `slice_budget_reached`, 8 files / 478 lines; consent granted by session default): lineage `review-7c32f8e8b07f7cc7`, approved, acknowledged (burned). 2 SUGGESTION → #118, fixed now by the T4 AGY session.
  - Full `uv run pytest` on `5b0710b` (once, parent, `techcamp_verify`): 693 passed.
  - #118 fix `9490222` (52, tests). RDD `5b0710b..9490222` (medium, under budget but reviewed as the last slice; consent granted by session default): lineage `review-4bde0ff948bb1feb`, approved, acknowledged. 1 WARNING + 1 SUGGESTION (boundary test not at the exact edge; NaN test not isolating the filter) → #119, fixed now by the T4 AGY session.
  - #119 fix `d859b21` (42, tests: readings at 04:59/05:00 UTC; NaN next to a valid reading). RDD `9490222..d859b21`: lineage `review-34f9581ecd01b305`, approved, acknowledged. 1 SUGGESTION (test name contradicts its assertion) → #120, fixed now by the T4 AGY session.
  - #120 fix `2d7ed64` (28, tests: rename + NaN-only case). RDD `d859b21..2d7ed64`: lineage `review-438b8ce5e02176ea`, approved, acknowledged. 2 SUGGESTION (NaN-only test specificity, exact float equality) → #121, left in the tracker (owner decision 2026-09-27: test-only suggestions after six rounds on the same tests).
- [x] T8 Close — full `uv run pytest`, feature doc final, delivery (after owner approval).
  - Implementation closed 2026-09-27 at `2d7ed64`: 18 commits, ≈ 2,380 authored lines. Full `uv run pytest` ran once on `5b0710b` (693 passed); the three later commits only touch `tests/irrigation/test_run_daily_balance.py` (targeted: 46 passed, ruff/format/mypy green). Delivery (owner, 2026-09-27: push + PRs, merge later on the owner's word): 9 stacked-to-main PRs cut at commit boundaries, all ≤ 400 authored lines, no `size:exception`: #122 `e6f/01-plan` (100), #123 `e6f/02-domain-rules` (399), #124 `e6f/03-persistence` (291), #125 `e6f/04-forecast-flags` (272), #126 `e6f/05-skip-reasons` (200), #127 `e6f/06-job-api` (355), #128 `e6f/07-local-day` (165), #129 `e6f/08-sensor-assimilation` (366), #130 `e6f/09-assimilation-tests` (246). CI green on every PR; merged in order by the owner's word (retarget to `main`, merge commits); `main` @ `99a3bf4` CI green; #94–#103 and #115–#120 closed, #121 open.
- 2026-09-27: `main` CI on `803cd03` (merge of #122, docs only) failed with the teardown `DeadlockDetectedError` (flake #89, occurrence commented there); every later push, up to `99a3bf4`, is green.
- 2026-09-27: cleanup (owner): worktrees `e6-followups`, `e6f-t5|t6|t7`, `e6f-verify`, branches `fix/e6-followups`, `fix/e6f-t*`, `e6f/01..09` (local and remote), the AGY panes and container `techcamp-e6f-db` with its databases (`techcamp`, `techcamp_verify`, `techcamp_t5..t7`) removed.

Per task, record: route, writer session, commits (authored lines), RED line, checks, RDD lineage/outcome, issue closed.

## Delivery
- Strategy: `stacked-to-main` chained PRs of about 400 authored lines (AGENTS.md Workflow), format of E6 PRs #104–#111, `size:exception` noted when over. Push and PRs only after the owner says so; merge only when the owner says so (in order, retarget to `main`, merge commits). CI on every PR; a teardown deadlock on `DROP INDEX ix_reading_sensor_time` is #89: comment there and re-run.
- Branch `fix/e6-followups` from `origin/main` @ `b627b66`, worktree `~/proyectos/techcamp-v2-worktrees/e6-followups`; verify worktree `e6f-verify` (detached); container `techcamp-e6f-db` on :5438 with DBs `techcamp` (writer) and `techcamp_verify` (parent).

## Acceptance criteria
- [x] Every open finding in #94, #96, #97, #99, #100, #101, #102 fixed with a test, or proven already fixed (listed in Scope).
- [x] Docs updated for D1–D4 in the same commits as the code.
- [x] Every review round opened by this feature fixed and re-reviewed (#115–#120), except #121, left open by owner decision.
- [x] Full suite, ruff, format, mypy, lint-imports green; CI green on every PR and on `main` @ `99a3bf4`.

## Progress / evidence
- 2026-09-26: setup (owner-approved cleanup): E6 worktrees, `e6/0x-*` and `feat/e6-irrigation` branches (local + remote) and `techcamp-e6-db` removed after cherry-picking `495659f` → `d0d1728`; merged branch `fix/e5-82-consolidation-gap` (PR #93) removed. New worktree, CodeGraph index, DB and verify worktree created.
- 2026-09-26: findings mapped against `d0d1728`; feature doc created with proposed decisions D1–D5.
- 2026-09-27: owner approved D1–D5, T7 in scope, route switched to in-session `odd-worker`.

## Next step
None: feature closed. `main` @ `99a3bf4` is green. #121 stays in the tracker.
