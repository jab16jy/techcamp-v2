# TechCamp v2 — E11 Metrics and digital adoption index

## Objective
Deliver epic E11 from `docs/10-dag.md:74`: monthly digital adoption index per plot, crop-cycle
summary and the enrollment survey (`plot_baseline`), as specified in docs/11 §1–2, docs/03
§`plot_metric_monthly` y `crop_cycle_summary`, docs/04 §Riesgo, métricas y asistente and docs/07
§Indicadores de tecnificación. RF-13 (docs/01).

## Problem
Nothing exists: `server/src/techcamp/metrics/` holds empty `__init__.py` files; there is no
`plot_baseline`, `plot_metric_monthly` or `crop_cycle_summary` table, no metrics job, no metrics
API, no `web/src/features/metrics/`, and `/status` returns `digital_adoption_index: null`
(`home/application/plot_status.py:176-177`).

## Why
E11 feeds E15 (seminar demo: "Indicadores de tecnificación" and a non-null index in `/status`).
Impact is measured against each plot's enrollment survey (ADR-0024); without the survey there is
no impact metric.

## Scope
- In: D-T0.1…D-T0.13 below; server (schema, read views, baseline API, cycle summary, adoption
  index, monthly job + `/dev/jobs/metrics:run`, metrics read API, `/status` index) and web
  (enrollment survey form, indicators screen, index line on Inicio).
- Out: `field_record` and `relative_yield` values (v1 EVA data lost; follow-up issue);
  MADR alignment of the index (open owner decision, docs/11 §2); groups 3–5 of docs/11.

## Constraints
- Runs in parallel with E10 (`feat/e10-climate-risk`). E11 depends only on E6/E7/E8
  (docs/10-dag.md:46-48). Shared hazards: single Alembic head (whichever lands second rechains
  its `down_revision`) and `server/src/techcamp/worker.py` job registration.
- Lessons from E4–E10 (`../techcamp-v2-worktrees/e11-briefs/explore-lessons.md.out`): org filter
  on every query with isolation tests; America/Bogota days via `shared.dates`; missing evidence is
  `null`; per-item error containment in batch jobs; no transaction across I/O; frozen test
  clocks; unique OpenAPI schema names; commits ≤ ~400 authored lines, lockfiles apart;
  periodic tasks take `timestamp: int = 0`; savepoint around queueing-lock deferrals.
- `metrics` reads other modules' data through read-only SQL views (docs/05 §Reglas); plot access
  and cycles through the `farms` facade.

## Decisions (T0)
Owner approved 2026-10-02.
- D-T0.1 Module graph: add `metrics → farms` and `home → metrics` (docs/05).
- D-T0.2 `plot_metric_monthly`: PK `(plot_id, month)`, `org_id`, 4 nullable components, nullable
  index, `computed_at`; idempotent upsert (docs/03).
- D-T0.3 A component without evidence is `null`; the index splits 100 points evenly over the
  non-null components; all null → index null (docs/11 §2).
- D-T0.4 `monitoring`: expected = claimed seconds inside the month ÷ `interval_s`, summed over
  nodes; every received reading counts, any `quality` (docs/11 §2).
- D-T0.5 `decision`: days with `irrigate`/`postpone`/`not_needed`; `irrigate` followed when that
  day's `irrigation_mm` is within ±25 % of `depth_mm`; others followed when no irrigation that day.
- D-T0.6 `risk_management`: plot alerts opened in the month; timely = `occurred_on` between the
  local day of `opened_at` and the local day of `opened_at + 48 h`.
- D-T0.7 Calendar month in America/Bogota; one job on day 1 at 02:00 (cycle summaries, then the
  previous month's index); `/dev/jobs/metrics:run { day? }` (docs/04 §Operación, docs/10).
- D-T0.8 `crop_cycle_summary` persisted for `harvested`/`lost` cycles, computed on read for an
  active cycle; includes `loss_kg` and `loss_cop` (docs/03, docs/04).
- D-T0.9 `relative_yield` stays `null`; no `field_record` in E11; follow-up issue.
- D-T0.10 Roles: baseline `PUT` owner/technician, `GET` any member; org metrics owner/technician.
- D-T0.11 `PUT /plots/{id}/baseline` creates or replaces; `recorded_by` = caller; editable after
  cycles exist.
- D-T0.12 `OrgMetrics`: mean index, plots with index, monitored-plots ratio, harvested-cycles
  ratio, median hours to first reading; computed on read.
- D-T0.13 Web: one "Indicadores de tecnificación" screen (plot + org section for owner/technician),
  enrollment survey form in plot detail, index line on Inicio (`impeccable`); `/status` returns the
  latest `plot_metric_monthly` row.

## Tasks
Forecasts are authored lines (additions + deletions, generated excluded). Route = writer and reason.
Default writer: Herdr OpenCode (owner, 2026-10-02); AGY for quick units. Every writer runs in its
own worktree + branch + `just db-up` + CodeGraph index, branched from `feat/e11-metrics`.

- [x] T0 Decisions into owner docs: docs/03, 04, 05, 07, 11 (no ADR: no accepted decision
  changed; `domain-modeling` skill not installed, existing conventions followed). ~80. Route:
  parent inline (planning is never delegated).
- [x] T1 Schema: migration for `plot_baseline`, `plot_metric_monthly`, `crop_cycle_summary`
  (org_id, FKs, CHECKs), SQLAlchemy tables. ~200 forecast, **~1170 actual** (prod ~500, tests
  ~670; 5.9x: one revision per table plus a CHECK test per constraint). Route: Herdr OpenCode.
  Commits bf70633, aaab75b, 86fa67d, 67965ae (gate fix: NOT NULL yield/recorder, cycle→plot FK),
  3be6dd6 (#243). Merged 31c6b3e. Evidence: `just gate server/tests/metrics` 38 passed (lane and
  epic branch), `gate-lane` green on 5 commits. import-linter only checks layers, so the D-T0.1
  arrows are not enforced; reviewed by hand at each gate.
- [x] T2 Enrollment survey: `GET/PUT /plots/{plot_id}/baseline`, use cases, repository, roles,
  isolation tests. ~350 forecast, **~1270 actual** (prod ~470, tests ~800). Route: Herdr OpenCode.
  Commits c122b09 (domain + use cases), 43b41f8 (adapters + API), c91a1b9 (RDD bounded
  correction: prove PUT replaces), 8ba72ef (#244 follow-ups). Merged 0d4c0af. Evidence: parent
  docs comparison clean (roles 403, cross-org 404, 422 bad crop, upsert, `recorded_by` = caller,
  org filter); `just gate server/tests/metrics server/tests/test_openapi_schema_names.py` 70 + 1
  passed on the lane (parent rerun) and on the epic branch after merge; `gate-lane 31c6b3e` green
  on all 4 commits. RED first line: `test_an_owner_saves_and_reads_the_survey` 404 == 200 (route
  not mounted). Baseline port lives in `metrics/application/baseline.py` (lane split with T3).
- [ ] T3 Read-only SQL views (migration) and the metrics source repository: readings expected vs
  received per node-month, logbook aggregates per cycle and per plot-month, recommendations vs
  irrigation per day, plot alerts with timely actions, stress days. ~400. Route: Herdr OpenCode.
  Depends T1.
- [ ] T4 Cycle summary: pure domain math (docs/11 §1) + use case (closed → persist, active →
  compute) + upsert. ~350. Route: Herdr OpenCode. Depends T3. Parallel with T5.
- [ ] T5 Adoption index: pure component math (D-T0.3…6) + monthly use case + upsert + latest-index
  query for `home`. ~400. Route: Herdr OpenCode. Depends T3.
- [ ] T6 Monthly job (day 1, 02:00; cycles then index; per-plot containment) +
  `/dev/jobs/metrics:run` + worker registration + periodic-schedule test. ~200. Route: Herdr
  OpenCode. Depends T4, T5.
- [ ] T7 Metrics read API: plot metrics, cycle summary, org metrics (D-T0.10, D-T0.12). ~350.
  Route: Herdr OpenCode. Depends T4, T5. Parallel with T6, T8.
- [ ] T8 `/status` index from `metrics` (D-T0.13). ~120. Route: Herdr AGY (quick). Depends T5.
- [ ] T9a Web enrollment survey form in plot detail (`impeccable`). ~300. Route: Herdr OpenCode.
  Depends T2.
- [ ] T9b Web indicators screen + index line on Inicio (`impeccable`). ~400. Route: Herdr
  OpenCode. Depends T7, T8.
- [ ] T10 Close: `just gate-release`, follow-up issue (`field_record` / `relative_yield`), delivery
  plan. Route: parent.

Forecast total ~3150 authored lines. Delivery strategy: stacked-to-main chained PRs of about
400 authored lines (AGENTS.md §Workflow), on the owner's word.

## Lanes
Wave 1: T1. Wave 2: T2 ‖ T3. Wave 3: T4 ‖ T5 ‖ T9a. Wave 4: T6 ‖ T7 ‖ T8. Wave 5: T9b. Wave 6: T10.
Parallel lanes only with disjoint files and no dependency (owner rule, E9).

## Review (RDD)
RDD on (global). OpenCode writers run their own RDD after the parent gate passes; AGY lanes are
reviewed by the parent.
- T0 docs (aaa9cfc): `review-38b1d335a3e1bb69` low, no lenses, approved and acknowledged.
- docs/11 zero-yield rule: `review-b356e7d583127108` low, approved and acknowledged.
- T1: `review-42eef0b7423540e0` medium, one lens (`review-reliability`), approved, authority
  burned by the writer. 3 WARNINGs → #243: zero-denominator resolved by the docs/11 rule (T4
  applies it), CHECK text drift and missing rejection tests fixed in 3be6dd6 (`Refs #243`).
  Note: the provider computed the base itself (a2bae6b, not the fork point), so the T1 review
  also covered T0's docs.
- T2: `review-af6d2f2a10ceb950` medium, one lens (`review-reliability`), owner consent granted,
  1 CRITICAL fixed in the bounded correction c91a1b9 (16 lines, targeted validation approved),
  authority burned (consumed revision `sha256:8bee66f8…`). 5 non-blocking → #244: 002–005 fixed in
  8ba72ef (`Refs #244`), 006 (anonymous-request test) left for T7. Full report:
  `../techcamp-v2-worktrees/e11-briefs/e11-t2-rdd.md`.
- Epic-branch candidates the stop hook raises after each merge (T0+T1, then T0+T1+T2): owner
  declined each (`declined_this_candidate`); each lane already has its own review.

Findings rule (owner, 2026-09-30): blocking → bounded correction; 1–2 non-blocking → one issue per
round; 3+ → fix the most important with `Refs #N`, file the rest.

### How a lane runs RDD (the T2 procedure; binding for T4 onwards)
1. After the parent gate passes, the writer runs
   `gentle-ai review assess --cwd <lane> --agent opencode --base-ref <fork point> --committed-only --json`.
   Never start from the selectorless `review status`: it binds the merge-base with `main`
   (a2bae6b) and freezes the whole epic as the candidate (T1, T3 attempt 1).
2. Execute the returned `next_transition.command` verbatim (the preflight STATUS with the same
   selectors), then the START it returns.
3. A consent envelope is relayed to the parent complete; the parent relays it to the owner with
   `AskUserQuestion`; the writer runs only the invocation of the chosen answer, verbatim.
4. Each `review.capture-result` slot is dispatched once through OpenCode's `subagent` tool with the
   provider's agent and prompt copied verbatim. Never author, edit or rewrite reviewer output.
5. `output_refused` means Go refused the reviewer's JSON at admission, not that the agent is
   missing. Read the newest file in `<main repo>/.git/gentle-ai/rejected-results/<lineage>/` for the
   exact reason (T3: finding ids without the `R3-` lens prefix; `proof_path_out_of_scope`). Run the
   bound STATUS and relaunch only when it re-offers the same slot.
6. `correction_required` → follow its `status_continuation` (one bounded correction);
   approval → run the exact `review.acknowledge-approved` once and report the
   `gentle-ai.review-acknowledged/v1` envelope.
7. The writer writes its full report (outcome, ack envelope, every finding with id, severity,
   location, full claim) to `../techcamp-v2-worktrees/e11-briefs/<lane>-rdd.md`; the parent files
   the follow-up issue and records the lineage here.

## Progress
- 2026-10-02: worktree `../techcamp-v2-worktrees/e11-metrics` (`feat/e11-metrics` from
  `origin/main` a2bae6b), CodeGraph index, DB `techcamp-db-e11-metrics`. Explorers (Herdr
  OpenCode docs map, AGY lessons) → `../techcamp-v2-worktrees/e11-briefs/*.md.out`. T0 written.

## Next step
T2 merged. T3 (`e11-t3`) gate passed; its RDD (`review-9b9197b7eee80109`) is relaunching the re-offered slot after an admission rejection. Then wave 3: T4 ‖ T5 ‖ T9a.
