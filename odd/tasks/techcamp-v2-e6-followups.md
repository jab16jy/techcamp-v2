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
- D5 #94 R3-003 / #102 R3-status-zero-raw-edge: with `RAW <= 0` (degenerate soil) status and decision are pinned by explicit tests; the exact outcome is chosen from docs/06 §5 in T1 and recorded here.

## Route and checks
- TDD: on (owner decision 2026-09-22, AGENTS.md "Testing"). RED → GREEN → REFACTOR per issue, observed with targeted runs only (`uv run pytest tests/irrigation/test_x.py::test_name` or the issue's test file). RED must fail on an assertion or a missing name, not only a missing module. Runner: `uv run pytest` in `server/`.
- Fast checks per commit: targeted tests of touched modules, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`.
- Full `uv run pytest` runs once, at the end of implementation, before delivery; on failure only the failing tests are re-run.
- Parent gate per commit: on the committed SHA in detached worktree `e6f-verify`, DB `techcamp_verify` — targeted module tests (`tests/irrigation`, plus `tests/farms`/`tests/telemetry`/`tests/weather` when touched), ruff, format, mypy, lint-imports, and a diff-vs-owner-doc check. Quality issues found here are fixed immediately by the same writer session.
- Route (owner, 2026-09-27): delegated direct, Claude Code `odd-worker` subagent (Sonnet) inside the orchestrator session instead of Herdr/OpenCode; one fresh worker per task number, a/b sub-units share it. `DATABASE_URL=postgresql+asyncpg://techcamp:techcamp@localhost:5438/techcamp`. Test runs (writer and parent) go in the background.
- Every brief: Step 0 CodeGraph first (`codegraph status`, then `codegraph_explore` / read-only CLI); docs to read; items; TDD rule; checks; commit message; stage only own files; no AI attribution; do not push. Briefs live in the worktree as `.git-brief-*.md` (excluded via `info/exclude`).
- Units over ~300 authored lines split into a/b sub-units, one commit each.

## Review (RDD)
- On (global). With the in-session worker, the parent runs RDD as `claude-code` after each work-unit commit: `gentle-ai review assess --cwd <worktree> --agent claude-code --base-ref <last reviewed boundary> --committed-only --json` per work-unit commit on a clean worktree and follows `next_transition` verbatim. First boundary: branch point `b627b66` (the doc commit `d0d1728` is passive).
- Consent: fixes, not features — the standing grant does not apply; the parent relays the full `consent/v3` envelope to the owner.
- Before acknowledging, the parent records every finding's `.claim` from `reviewer_results`.
- Blocking findings: fixed in the bounded correction. Non-blocking findings of this feature: one issue per review round (`review-follow-up`, `epic:e6`, `area:server`, `type:*`), fixed right away by a worker resumed with the same context (`Refs #N`), then reviewed again until clean.
- `under_budget` stays pending in the slice and is recorded.

## Tasks
Forecast ≈ 1,300 authored lines (tests included).

- [ ] T1 Domain rules — #94 (R3-001..005) + #102 (R3-status-zero-raw-edge, R3-local-today-untested, R3-router-enum-fallback-removed-unproved) — grouped because #94 R3-003 and #102 R3-status-zero-raw-edge touch the same function (`compute_water_balance_status`). Files: `irrigation/domain/models.py`, `tests/irrigation/test_domain_models.py`, `tests/irrigation/test_api.py` (enum contract test). ≈ 250 lines.
  - Clamp assimilated/observed `Dr` to 0..TAW (docs/06 §5 "Dr modelado" range); unit-suffix `is_sensor_depth_representative` params (cm) with a test; guard `RAW <= 0` (D5); boundary tests at `Dr == RAW`, `Dr == 0.8·RAW`, efficiency fallback, `duration_min = None`, `stage_for_cycle_day` ValueError, empty stages; stage-name mismatch fails loudly; `local_today` early-UTC and naive-input tests; API test pinning enum members.
- [ ] T2 Persistence — #96 (upsert-returning-stale, balance-get-org-filter-untested, migration-cycle-untested, advice-silent-drop, upsert-ignores-model-plot-day). Files: `irrigation/adapters/repositories.py`, `tests/irrigation/test_repositories.py`. ≈ 150 lines.
- [ ] T3 Daily balance use case — #97 (missing-forecast D1, low-confidence-untested incl. 24 h boundary, skip-branches-untested, atomic-write-unproved, empty-stages-indexerror). Files: `irrigation/application/run_daily_balance.py`, `tests/irrigation/test_run_daily_balance.py`, docs/06 §5, docs/04 Riego. ≈ 300 lines (split a: forecast + low-confidence + empty stages; b: skip branches + atomicity, if over budget).
- [ ] T4 Sensor assimilation — #99 (freshness anchor D2, local-day window + quality D3, daily-value-none, untested branches, weak assimilation assertion). Files: `run_daily_balance.py`, telemetry read port only if needed for raw readings with quality, tests, docs/06 §5. ≈ 250 lines.
- [ ] T5 Daily job — #100 (weather repo import from `weather.adapters.repositories`, fan-out test isolation, IntegrityError narrowed to the queueing-lock constraint). Files: `irrigation/adapters/jobs.py`, `tests/irrigation/test_jobs.py`. ≈ 80 lines.
- [ ] T6 Read API — #101 (tz default tests with an 00:00–05:00 UTC instant, range cap D4, `dependency_overrides.pop(get_now, None)`, 422 problem+json shape). Files: `irrigation/application/query_irrigation.py`, `tests/irrigation/test_api.py`, docs/04 Riego if wording needs it. ≈ 100 lines.
- [ ] T7 Local-day helper — #103: one pure helper in `shared/` (stdlib `zoneinfo`) used by weather and irrigation. ≈ 60 lines.
- [ ] T8 Close — full `uv run pytest`, feature doc final, delivery (after owner approval).

Per task, record: route, writer session, commits (authored lines), RED line, checks, RDD lineage/outcome, issue closed.

## Delivery
- Strategy: `stacked-to-main` chained PRs of about 400 authored lines (AGENTS.md Workflow), format of E6 PRs #104–#111, `size:exception` noted when over. Push and PRs only after the owner says so; merge only when the owner says so (in order, retarget to `main`, merge commits). CI on every PR; a teardown deadlock on `DROP INDEX ix_reading_sensor_time` is #89: comment there and re-run.
- Branch `fix/e6-followups` from `origin/main` @ `b627b66`, worktree `~/proyectos/techcamp-v2-worktrees/e6-followups`; verify worktree `e6f-verify` (detached); container `techcamp-e6f-db` on :5438 with DBs `techcamp` (writer) and `techcamp_verify` (parent).

## Acceptance criteria
- [ ] Every open finding in #94, #96, #97, #99, #100, #101, #102 fixed with a test, or proven already fixed (listed in Scope).
- [ ] Docs updated for D1–D4 in the same commits as the code.
- [ ] Every review round opened by this feature fixed and re-reviewed clean.
- [ ] Full suite, ruff, format, mypy, lint-imports green; CI green on every PR.

## Progress / evidence
- 2026-09-26: setup (owner-approved cleanup): E6 worktrees, `e6/0x-*` and `feat/e6-irrigation` branches (local + remote) and `techcamp-e6-db` removed after cherry-picking `495659f` → `d0d1728`; merged branch `fix/e5-82-consolidation-gap` (PR #93) removed. New worktree, CodeGraph index, DB and verify worktree created.
- 2026-09-26: findings mapped against `d0d1728`; feature doc created with proposed decisions D1–D5.
- 2026-09-27: owner approved D1–D5, T7 in scope, route switched to in-session `odd-worker`.

## Next step
T1 to a fresh `odd-worker`.
