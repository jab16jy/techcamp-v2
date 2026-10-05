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
- D-T3.1 (owner, 2026-10-03) The `water_stress` action of `risk_management` is **plot-scoped**:
  a registered irrigation on that plot inside the 48 h window counts, with **no crop-cycle
  qualifier**, so an entry with `crop_cycle_id = null` counts too. Ruling on the escalated
  CRITICAL `R3-reliability.alert-action.cross-plot`: `alert` carries no `crop_cycle_id`, and
  scoping by cycle would discard actions from entries the phone sent without one. Written into
  [11-metricas §2](../docs/11-metricas.md) and [ADR-0025](../docs/adr/0025-la-accion-de-water-stress-es-de-la-parcela.md);
  the SQL is unchanged.
- D-T4.1 (owner, 2026-10-05) RDD without slice budget: a candidate may span several
  work-unit commits; `under_budget` slices with no offered START are covered by the
  combined candidate instead of PR slices.
- D-T4.2 (owner, 2026-10-05) Single authorization channel: `consent/v3` and any audited
  authorization go through the orchestrator (complete envelope, STOP, wait); never
  `ask_user_question` to whoever is at the keyboard.
- D-T4.3 (owner, 2026-10-05) A work unit stays ≤ ~400 authored lines (additions + deletions,
  generated excluded), and complementary functions ship as **separate** work units — hooks/queries
  apart from UI, screen apart from route wiring, read apart from write. Review granularity follows
  work units, so commit granularity and review granularity are the same boundary (AGENTS.md
  §Workflow; T1 forecast 200 → 1170 actual is the counterexample this rule answers).
- D-T0.10 Roles: baseline `PUT` owner/technician, `GET` any member; org metrics owner/technician.
- D-T0.11 `PUT /plots/{id}/baseline` creates or replaces; `recorded_by` = caller; editable after
  cycles exist.
- D-T0.12 `OrgMetrics`: mean index, plots with index, monitored-plots ratio, harvested-cycles
  ratio, median hours to first reading; computed on read.
- D-T0.13 Web: one "Indicadores de tecnificación" screen (plot + org section for owner/technician),
  enrollment survey form in plot detail, index line on Inicio (`impeccable`); `/status` returns the
  latest `plot_metric_monthly` row.
- D-T7.1 (owner, 2026-10-05) OrgMetrics ships partial: `mean_digital_adoption_index`,
  `plots_with_index` and `monitored_plots_ratio` from `list_for_org_month`; `harvested_cycles_ratio`
  and `median_hours_to_first_reading` ship as `null` (missing-evidence-is-null convention) until a
  new lane lands the two `metrics_*` views plus `source_repository` methods (migration +
  frozen-port change, outside T7's surfaces). T7 closes against this decision.
- D-T9.1 (owner, 2026-10-05) T9b ships the plot section and the org section; the **cycle summary
  section is deferred**. Its endpoint needs a `crop_cycle_id` that `ActiveCycleView` does not expose
  (docs/04-api.md:64) and there is no cycle listing, so the web has no way to name a cycle; exposing
  the id is a separate server task plus a docs/04 change. Registered as a follow-up, not silently
  dropped.
- D-T9.2 (owner, 2026-10-05) The default month for both metrics queries is the **previous calendar
  month in America/Bogota**, computed client-side, matching the job's own cadence (day 1 at 02:00
  computes the prior month). No month picker in this lane; a null row renders "Sin datos este mes"
  (docs/07:147), never 0 (docs/11:57).

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
- [x] T3 Read-only SQL views (migration) and the metrics source repository: readings expected vs
  received per node-month, logbook aggregates per cycle and per plot-month, recommendations vs
  irrigation per day, plot alerts with timely actions, stress days. Route: Herdr OpenCode.
  Depended T1. Commits `7cc3dd7` (port), `51052a1` (builders), `09cef3f` (monitoring),
  `407ed2b` (record-keeping + decision), `230289c` (alert action), `429b293` (cycle totals),
  `371c0e7` (bounded correction) + close-out docs/tests (`823974d`, `a1941ba`, D-T3.1 ruling).
  Evidence: `uv run pytest tests/metrics` 61 passed, `gate-fast` green; RDD
  `review-82b031d38b733383` (close-out `371c0e7..HEAD`, 9 paths / 466 lines, reliability,
  4 WARNING, approved, receipt `d2c09ecd…`, burned); follow-up #248; report
  `../e11-briefs/e11-t3-rdd.md`. Merged `8a8cbc4`.
- [x] T4 Cycle summary: pure domain math (docs/11 §1) + use case (closed → persist, active →
  compute) + upsert. Route: Herdr OpenCode. Depended T3. Parallel with T5. Commits `f7d808e`
  (domain, 396), `d3f6e72` (use case, 440), `c01fd2e` (repo, 339). Evidence: unit 2
  `review-24ee776a961efc72` (reliability, 1 SUGGESTION, receipt `86019dee…`, burned) + lane
  combined `review-15dcaac22de5ade7` (`104608c..c01fd2e`, 1175 lines, 0 findings, receipt
  `cee683fa…`, burned, D-T4.1); follow-up #250; report `../e11-briefs/e11-t4-rdd.md`.
  Merged `465e1bd`; worktree/rama/DB de `e11-t4` eliminados.
- [x] T5 Adoption index: pure component math (D-T0.3…6) + monthly use case + upsert + latest-index
  query for `home`. Route: Herdr OpenCode. Depended T3. Commits `9d9b3b2` (domain, 590),
  `f05e8db` (use case, 539), `d8de405` (repo, 418) + correction `f45b0bc`→`ce779ac` + fix
  `e8d975b` (`Refs #251`). Evidence: three burned receipts (`781b21ad…`, `3ea9b1fd…`,
  `74a1e77a…`); fix `under_budget` without lineage (terminal disposition); follow-up #251
  (6 WARNING, #1 fixed in `e8d975b`); report `../e11-briefs/e11-t5-rdd.md`. Merged `e04cde3`;
  worktree/rama/DB de `e11-t5` eliminados.
- [x] T6 Monthly job (day 1, 02:00; cycles then index; per-plot containment) +
  `/dev/jobs/metrics:run` + worker registration + periodic-schedule test. Route: Herdr
  OpenCode. Depended T4, T5. Commit `a207c95` (single work unit: jobs 265 + dev trigger 56 +
  worker/main +2/+2, tests 402 — prod 325 ≤ D-T4.3 budget, one unit). Evidence: `just gate
  server/tests/metrics` 194 passed (lane and epic branch after merge); RED observed: route not
  mounted / `app.periodic` absent, plus one genuine assertion failure
  (`assert Decimal('0') is None` — the test was wrong: `record_keeping` is calendar math and never
  null, docs/11 §2); docs cited: docs/10:178-180, docs/04:261, docs/03:128-135/438-443,
  docs/11 §2, D-T0.2/0.3/0.7/0.8/0.13, ADR-0012, ADR-0021. RDD `review-a6aca8f6f1f43b9c`
  (reliability, 1 WARNING `R3-DATE-MIN`, approved, authority burned); follow-up #252; report
  `../e11-briefs/e11-t6-rdd.md`. Merged `1765cf5`; worktree/rama/DB de `e11-t6` eliminados.
- [x] T7 Metrics read API: plot metrics, cycle summary, org metrics (D-T0.10, D-T0.12). Route:
  Herdr OpenCode. Depended T4, T5. Commits `00869b8` (plot metrics), `21d909e` (cycle summary),
  `821f8d7` (org metrics, D-T7.1 partial — a third unit, kept separate because it is a different
  feature with its own roles). Evidence: `just gate server/tests/metrics
  server/tests/test_openapi_schema_names.py` 219 + 1 passed (lane and epic branch after merge),
  `gate-lane` 3/3; RED observed: `assert 404 == 200` on the three new routes (not mounted) and the
  006 of #244 (anonymous request) had no test at all until this lane; docs cited: docs/04:233-237,
  docs/03:432-443, docs/11 §1-2, docs/09#seguridad, D-T0.8/0.10/0.12, D-T7.1. RDD
  `review-fd40d9db212cbb50` (medium, reliability, 1 WARNING `R3-cycle-status-fallback` refuted as
  unreachable premise, approved, authority burned — the frozen target already spanned all three
  units); no follow-up issue; report `../e11-briefs/e11-t7-rdd.md`. Merged `3f613ca`;
  worktree/rama/DB de `e11-t7` eliminados.
- [x] T8 `/status` index from `metrics` (D-T0.13). Route: Herdr OpenCode (quick lane).
  Depended T5. Commit `2786e4c` (single work unit, 5 files, 274 líneas ≤ D-T4.3). Evidence:
  `just gate server/tests/home server/tests/metrics/test_monthly_repository.py` 39 + 14 passed
  (lane and epic branch after merge); RED observed twice: `ImportError: cannot import name
  'DigitalAdoption' from 'techcamp.home.application'`, then with the router reverted
  `TypeError: build_plot_status() missing 1 required keyword-only argument: 'metrics'`; 7 new
  tests (`month` order not `computed_at`, no month → null, null index ≠ 0, cross-org negative);
  docs cited: docs/04:63-71/89, docs/03:438, docs/07:152, docs/09#seguridad, docs/11 §2,
  D-T0.1/0.2/0.13. RDD `review-727e456fcf63bb7d` (reliability, 0 findings, approved,
  authority burned); no follow-up issue; report `../e11-briefs/e11-t8-rdd.md`. Merged `997a016`;
  worktree/rama/DB de `e11-t8` eliminados.
- [ ] T9a Web enrollment survey form in plot detail (`impeccable`). ~300. Route: Herdr OpenCode.
  Depends T2.
- [x] T9b Web indicators screen + index line on Inicio (`impeccable`). Route: Herdr OpenCode.
  Depended T7, T8 (waves collapsed). Commits after re-slice: `f2d378e` (hooks + test),
  `7270d60` (figures as ruled rows), `876de18` (screen), `06ed187` (route under Más),
  `2fb59d6` (Inicio line, `CACHE_BUSTER` 3→4), `8155512` (post-review docstring fix, F2).
  Lane re-slice: U2 landed at 695 lines and was split into component / container / route wiring
  (D-T4.3), no test or comment dropped. Evidence: `just gate web/src/features/metrics
  web/src/features/plot-status web/src/app` EXIT=0 (metrics 25, plot-status 86, app 15),
  `gate-lane` 5/5; RDD `review-4b39d1bd807fbd23` (reliability, 0 native findings, approved,
  authority burned); follow-up #255; report `../e11-briefs/e11-t9b-rdd.md`. Merged `228666d`.
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

### How a lane runs RDD (the T2 procedure, corrected by the T3 resolution; binding for T4 onwards)
1. After the parent gate passes, the writer runs
   `gentle-ai review assess --cwd <lane> --agent <runtime> --base-ref <fork point> --committed-only --json`.
   Never start from the selectorless `review status`: it binds the merge-base with `main`
   (a2bae6b) and freezes the whole epic as the candidate (T1, T3 attempt 1).
2. Execute the returned `next_transition.command` verbatim (the preflight STATUS with the same
   selectors), then the START it returns.
3. A consent envelope is relayed to the parent complete; the parent relays it to the owner
   through the orchestrator channel; the writer runs only the invocation of the chosen answer,
   verbatim. **D-T4.2 (owner, 2026-10-05): single authorization channel** — `consent/v3` and any
   audited authorization go through the orchestrator (complete envelope, STOP, wait); never
   `ask_user_question` to whoever is at the keyboard (T4/T5 keyboard grants were ratified
   post-hoc once; since then the hard rule holds).
4. The review granularity is **work-unit commits, not a line limit**. The native review candidate
   is one work unit or several work units forming one coherent behavior, **never the accumulated
   lane branch and never past the lane**. Scope with `--base-ref` at the candidate's first commit,
   not the lane fork point (T3: the 2353-line lane stalled; the 466-line close-out `371c0e7..HEAD`
   burned). **D-T4.1 (owner, 2026-10-05): RDD without slice budget** — a candidate may span several
   work-unit commits; `under_budget` slices with no offered START are covered by the combined
   candidate instead of PR slices (T4: units 1+3 `under_budget`, lane-combined `104608c..c01fd2e`
   1175 lines approved with 0 findings). Do not overshoot: one lane max, no merge commits, no
   unrelated tasks. `--base-ref` is EXCLUSIVE: it covers `X..HEAD` without `X` (verified:
   `f7d808e..HEAD` = 779 lines).
5. Runtime rule: **Pi runs RDD (host-relay `pi_host_relay`)**. OpenCode's relay contract is
   fixed at `gentle-ai.opencode-relay/v2-staged` and refuses `workspace`-projection bindings
   (`binding_mismatch`); Claude Code is org-blocked on this machine.
   `OpenCode writers run their own RDD` is superseded for the review step only — OpenCode still
   writes the code.
6. Each `review.capture-result` slot is dispatched once with the provider's agent and prompt
   copied verbatim. Never author, edit or rewrite reviewer output. Finding `id` is optional and
   harness-assigned — never write it (T3: invented ids got the whole capture rejected).
7. Verify receipts in `review-transactions/terminal-consumption/v1/`, **not** by listing `v2/`.
   On burn Go prunes the lineage directory; absence of the directory is the signature of a
   successful burn. Empty `rejected-results/` means the capture was admitted.
8. The bound STATUS needs all three together: `--agent <runtime>` + `--lineage` +
   `--repository-context <rc>`. That flips `applicability` from `unrelated` to `current_target`.
9. The admitted result body does NOT survive the burn — copy findings to the issue/doc **before**
   acknowledging.
10. `correction_required` → follow its `status_continuation` (one bounded correction);
   approval → run the exact `review.acknowledge-approved` once and report the
   `gentle-ai.review-acknowledged/v1` envelope.
7. The writer writes its full report (outcome, ack envelope, every finding with id, severity,
   location, full claim) to `../techcamp-v2-worktrees/e11-briefs/<lane>-rdd.md`; the parent files
   the follow-up issue and records the lineage here.
11. Each lane attaches ODD evidence that what it built is what the feature doc asks: the task IDs
    it closes with observed outcomes and checks (RED first line, GREEN, REFACTOR), the exact gate
    commands with observed results (including seeds for `pytest-randomly` failures), every work-unit
    commit identity, and the docs/ sections or ADRs it cites. The parent records the commits and the
    verification evidence in this document before merging; checkboxes grant no approval or receipt.
12. Wave 4 (T6 ‖ T7 ‖ T8) review plan: T8 (~120) one candidate; T6 (~200) one candidate, two only
    if two genuinely separate work units emerge; T7 (~350) per work unit, combined only when the
    units form one coherent behavior per step 4. Pi (host-relay) runs every RDD; OpenCode writers
    never run reviews.

### T3 — lineage `review-82b031d38b733383`, **aprobado, autoridad quemada** (2026-10-05)

| Item | Resultado |
|---|---|
| Alcance | Work unit de cierre `371c0e7..HEAD`, 9 paths / 466 líneas (`assess --base-ref 371c0e7`) |
| Lente | `review-reliability`, medium risk |
| Hallazgos | **4 WARNING**, ningún BLOCKER ni CRITICAL |
| Recibo | `terminal-consumption/v1`, target `sha256:d2c09ecd60ad97b55d07de7fb2e1db4d5cfac4ab3ee3c62024ce163507dfd07a` (base tree `dff067d3` → candidate `7d2f97d7`) |
| Follow-up | Issue #248 (`review-follow-up`, `area:server`) con los 4 WARNING |
| Verificación | `uv run pytest tests/metrics -q` → 61 passed; `gate-fast` verde |

Corrido por un agente Pi fresco (runtime host-relay), lineage nuevo, consentimiento del dueño.
El WARNING 4 (docstring apuntando a ADR-0024) ya quedó corregido en esta rama antes del merge.

### T3 — lineage `review-9b9197b7eee80109`, escalated (sin recibo)

| Item | Resultado |
|---|---|
| Alcance | 6 commits de T3, 11 archivos / 1841 líneas (`assess --base-ref 31c6b3e`) |
| Lente | `review-reliability`, medium risk, `correction_budget: 200` |
| Hallazgos | **12** — 2 CRITICAL, 9 WARNING, 1 SUGGESTION |
| Corrección acotada | `371c0e7` (+71 líneas) |
| Validación dirigida | **rechazó** la corrección → `state: escalated`, `native_stop_required` |
| Authority | **no quemada**; sin envelope de acuse |

`review-5fb63a9414658ee5` (primer intento, base `a2bae6b`, 24 archivos / 3301 líneas) también quedó
escalado y sin tocar. Los tres rechazos iniciales `output_refused` **no** eran un agente
indespachable: el revisor sí corría y Go rechazaba su salida en la admisión
(`reviewer finding ID does not match the native ASCII schema` — los ids deben ser `^R[1-4]-…` con el
prefijo del lente, `R3-` aquí; y `proof_path_out_of_scope` en el primer intento). El schema dice que
el id es opcional y se lo asigna el harness.

**CRITICAL `R3-reliability.alert-action.cross-plot` — descargado con ruling, no con código.** El
validador rechazó `371c0e7` por una regla de proceso (*"a test that pins the reported behaviour does
not remove the reported behaviour"*), no porque el SQL estuviera mal. Ruling del dueño 2026-10-03
(**D-T3.1**): la acción de `water_stress` es **plot-scoped**, sin qualifier de ciclo, y una entrada
con `crop_cycle_id = null` cuenta. Escrito en [11 §2](../docs/11-metricas.md) y
[ADR-0025](../docs/adr/0025-la-accion-de-water-stress-es-de-la-parcela.md); **el predicado no cambió**
(solo el docstring de la vista). Commit de doc: `823974d`; commit de pruebas: `a1941ba`.

Commits de T3, en orden: `7cc3dd7` (port), `51052a1` (builders), `09cef3f` (monitoring),
`407ed2b` (record-keeping + decision), `230289c` (alert action), `429b293` (cycle totals),
`371c0e7` (corrección acotada), más los dos de cierre.

Los 10 hallazgos no bloqueantes **no** se corrigieron aquí: el cuerpo del issue queda redactado abajo
y su creación es del orquestador. Nota sobre el conteo: el revisor emitió 12 hallazgos y el refutador
dejó 1 en `fix_finding_ids`; de los 11 restantes, 4 quedaron cubiertos por el commit `a1941ba` y 7
siguen vivos (5 sin base en el modelo + `water-balance-date` y la cota del mes en curso).

#### Issue propuesto (no abierto) — `review-follow-up`, `epic:e11`, `area:metrics`, `type:tech-debt`

**Título:** E11 T3: cuatro WARNING no bloqueantes de la revisión aprobada (`review-82b031d38b733383`)

**Cuerpo:**

RDD del work unit de cierre de T3 (`371c0e7..HEAD`, 9 paths / 466 líneas),
lineage `review-82b031d38b733383`: captura admitida, **aprobado, autoridad quemada**.
El revisor emitió 4 WARNING, ningún BLOCKER ni CRITICAL. Ninguno requiere corrección
acotada: por la política congelada solo BLOCKER/CRITICAL la exigen, y por la regla de
findings del repo (1–2 no bloqueantes → un issue por ronda) se recogen aquí.

El CRITICAL de la ronda anterior (`R3-reliability.alert-action.cross-plot`,
lineage `review-9b9197b7eee80109`, escalado) quedó descargado por el ruling del dueño
**D-T3.1** (ver §Review de esta feature doc): la acción de `water_stress` es plot-scoped,
escrito en docs/11-metricas.md §2 y en ADR-0025, SQL sin cambios.

**Los 4 WARNING (vivos):**

1. `test_monitoring_view.py:206` — el test del mes en curso contrasta los segundos
   transcurridos contra la constante de 30 días (`2_592_000`). En el último día de
   cualquier mes de 31 días en America/Bogota ese cargo llega a ~2_688_000 y la
   aserción falla. La cantidad bajo prueba depende del calendario; la cota no.
2. `test_monitoring_view.py:200` — `elapsed_full_month` mide los segundos que tardó
   el propio test en correr, no la longitud del mes en curso. Solo prueba que el test
   terminó en menos de treinta días; el docstring que promete que un nodo "nunca
   alcanza la longitud completa del mes" no tiene aserción detrás.
3. `test_cycle_totals_view.py:269` — el docstring dice que la aserción es una cota y
   no un conteo exacto porque `now()` vive en SQL, pero el test fija el conteo exacto
   `water_stress_days == 3`. Una corrida que cruce la medianoche de Bogotá entre el
   setup y la query admite la fila insertada para `today + 1` y el conteo da 4.
4. Migración `0c67563f8d20` docstring — decía que D-T3.1 está explícito en ADR-0024,
   archivo que el candidato no tocaba (bytes idénticos en base y candidato). El texto
   vive en ADR-0025. **Ya corregido después de la revisión** (commit pendiente en la
   rama, junto con el mismo puntero en esta feature doc): el registro que impide
   re-levantar el CRITICAL ahora apunta al documento que sí lo lleva.

### T5 — units `review-31fa8e99789190a3`, `review-466f6005dbbfc761`, `review-45c7b4986f91457c`, **aprobado, autoridad quemada** (2026-10-05)

| Item | Resultado |
|---|---|
| Unidades | `9d9b3b2` dominio (590), `f05e8db` use case (539), `d8de405` repo (418) + corrección `f45b0bc`→`ce779ac` + fix `e8d975b` (`Refs #251`) |
| Unit 1 | medium, 3 WARNING, recibo `781b21ad…` |
| Unit 2 | 2 WARNING, recibo `3ea9b1fd…` (consent ratificado post-hoc por el owner) |
| Unit 3 | 1 CRITICAL (`R3-cross-org-upsert`, corregido en acotada ruteada por scope: revert a 2 paths) + 1 WARNING, recibo `74a1e77a…` |
| Fix #251 | `R3-UncheckedMonitoringInterval`, 59 líneas, `under_budget` sin lineage (disposición, no fallo) |
| Follow-up | Issue #251 (`review-follow-up`, `epic:e11`, `area:server`, `type:chore`) con los 6 WARNING; el #1 ya resuelto en `e8d975b` |
| Merge | `e04cde3` a `feat/e11-metrics`; worktree/rama/DB de `e11-t5` eliminados tras verificar |

Rung by Pi (host-relay), lineages nuevos tras el `binding_mismatch` del self-review OpenCode.
Ruta elegida por el owner entre revert-2-paths / recover / dejar-así: revert (recomendación
del orquestador por la convención cross-org-404). Consent del teclado ratificado por el owner;
desde D-T4.2 todo consent va por el canal del orquestador (T5 unit 1 ya lo cumplió).

### T4 — unit 2 `review-24ee776a961efc72` + lane completa `review-15dcaac22de5ade7`, **aprobado, autoridad quemada** (2026-10-05)

| Item | Resultado |
|---|---|
| Unidades | `f7d808e` dominio (396), `d3f6e72` use case (440), `c01fd2e` repo (339) |
| Unit 2 | medium, `review-reliability`, 1 SUGGESTION (`R3-missing-plot-path-coverage`), cero correcciones, recibo `86019dee…f3a5e` |
| Units 1+3 | `under_budget` sin lineage (provider no ofrece START); el combinado de 836 se declinó por sobre-presupuesto (regla vigente ese día) |
| Lane completa | `104608c..c01fd2e`, 6 paths / 1175 líneas, **0 findings**, recibo `cee683fa…68f72f` (regla sin-presupuesto del dueño, ver Decisiones) |
| Follow-up | Issue #250 (`review-follow-up`, `epic:e11`, `area:server`, `type:chore`) con el SUGGESTION (la lane completa no lo re-levantó: registrado no resuelto) |
| Merge | `465e1bd` a `feat/e11-metrics`; worktree/rama/DB de `e11-t4` eliminados tras verificar |

Rung by Pi (host-relay). El self-review OpenCode murió en captura con
`opencode_review_transport_relay_refused (reason: binding_mismatch)`, prompt
byte-idéntico: el relay v2-staged no ejecuta proyecciones `workspace` (precedente T3,
confirmado ×2 más en T4/T5). Consent y abandon salieron al teclado y el owner los
ratificó post-hoc (granted sin verificar); desde entonces rige la REGLA DURA: consent y
autorizaciones auditadas solo por el canal del orquestador. El id de lineage deriva del
`target_identity` (mismo target ⇒ mismo id tras `abandon` + re-mint con `runtime_agent: pi`).

### T8 — lineage `review-727e456fcf63bb7d`, **aprobado, autoridad quemada** (2026-10-05)

| Item | Resultado |
|---|---|
| Alcance | Work unit único `2786e4c`, 5 paths (`home/application/plot_status.py` + `__init__.py` export, `home/adapters/api/router.py`, 2 tests) |
| Lente | `review-reliability`, 0 findings, correction budget intacto |
| Recibo | `terminal-consumption/v1`, target `sha256:0c5042f2…`, lineage `review-727e456fcf63bb7d` |
| Follow-up | Ninguno (cero findings); handoff a T9b: `web/src/lib/api/schema.d.ts` sigue `null` hasta `npm run gen:api` |
| Merge | `997a016` a `feat/e11-metrics`; gate post-merge 39 + 14 verde |

Rung by Pi (host-relay), base-ref exclusivo `442ef91`. Surface delta declarada (2 líneas de export
en `home/application/__init__.py`): in-scope por convención del módulo, sin flag del revisor.
Full report: `../e11-briefs/e11-t8-rdd.md`.

### T6 — lineage `review-a6aca8f6f1f43b9c`, **aprobado, autoridad quemada** (2026-10-05)

| Item | Resultado |
|---|---|
| Alcance | Work unit único `a207c95`, 6 paths (jobs, dev trigger, worker, main, 2 tests) |
| Lente | `review-reliability`, 1 WARNING (`R3-DATE-MIN`: dev trigger acepta `0001-01-01`, el worker desborda; solo ese borde absurdo, el cron 02:00 nunca lo recibe) |
| Recibo | `terminal-consumption/v1`, target `sha256:3315cf1c…`, lineage `review-a6aca8f6f1f43b9c` |
| Follow-up | Issue #252 (`review-follow-up`, `epic:e11`, `area:server`, `type:bug`) con el WARNING |
| Merge | `1765cf5` a `feat/e11-metrics`; gate post-merge 194 verde |

Rung by Pi (host-relay), base-ref exclusivo full `442ef91…`. Full report:
`../e11-briefs/e11-t6-rdd.md` (+ findings JSON pre-burn).

### T7 — lineage `review-fd40d9db212cbb50`, **aprobado, autoridad quemada** (2026-10-05)

| Item | Resultado |
|---|---|
| Alcance | 3 work units en un candidato: `442ef91..821f8d7`, 5 paths / 1404 líneas, medium |
| Lente | `review-reliability`, 1 WARNING (`R3-cycle-status-fallback`) **refutado**: `CropCycleStatus` solo tiene ACTIVE/HARVESTED/LOST, la rama computada solo alcanza ACTIVE |
| Recibo | `terminal-consumption/v1` `458d07fc…78d9`, target `sha256:b995bd95…7352d2`, lineage quemada |
| Follow-up | Ninguno; 2 figuras de OrgMetrics quedan `null` hasta la lane de D-T7.1 |
| Merge | `3f613ca` a `feat/e11-metrics`; lane (worktree+rama+DB) eliminada |

**Granularity lesson (owner ruling 2026-10-05).** El brief pidió 3 lineages por work unit; el
orquestador lo corrigió a **un candidato combinado** porque el scope congelado de cada unidad
alcanza HEAD (U1 surfaces un hallazgo de U2, y el freeze de U1 resultó ser los 1404 renglones de
toda la lane). El `assess` combinado devolvió `review_due: false / already_reviewed` con el mismo
`target_identity`: el recibo de U1 ya cubría la lane entera, así que no hubo segunda revisión ni
segundo lineage. Per-unit scoping no aísla; batchear una feature coherente en un candidato.

### T9b — lineage `review-4b39d1bd807fbd23`, **aprobado, 0 findings, autoridad quemada** (2026-10-05)

| Item | Resultado |
|---|---|
| Alcance | Lane completa `edf176b..8155512`, 12 files / ~1150 líneas, web only, un candidato combinado |
| Lente | `review-reliability`, 0 findings nativos |
| Recibo | `terminal-consumption/v1`, target `sha256:74841f65…`, lineage quemada, `mutation_outcome: committed` |
| F1 (MEDIUM) | `CACHE_BUSTER` 3→4 — el reviewer pidió revertirlo; **el dueño lo mantiene**: la forma persistida de `/status` cambió en T8 y esta lane es la primera que la lee en web, y el `?? null` evita el crash pero no el "Sin datos" sobre datos reales |
| F2 (LOW) | Corregido en `8155512`: el docstring invertía la dirección del desfase y citaba un instante que en Bogotá es 18:30 del día 30 |
| F3/F4/F6/F5 | Follow-up #255 (extraer `MetricRow`, rama de ausencia para las 2 ratios de D-T7.1, coma es-CO, ventana de 02:00) |
| Merge | `228666d`; gate post-merge EXIT=0 |

Impeccable aplicado de verdad y **verificado**: el context加载 hizo descartar `MetricTile` porque su
`status` es vocabulario del balance hídrico y "The Two Vocabularies Rule" (DESIGN.md:133) prohíbe
cruzar los dos vocabularios. El revisor validó esa decisión por código, no por el reporte del autor.
Full report: `../e11-briefs/e11-t9b-rdd.md`.

## Progress
- 2026-10-02: worktree `../techcamp-v2-worktrees/e11-metrics` (`feat/e11-metrics` from
  `origin/main` a2bae6b), CodeGraph index, DB `techcamp-db-e11-metrics`. Explorers (Herdr
  OpenCode docs map, AGY lessons) → `../techcamp-v2-worktrees/e11-briefs/*.md.out`. T0 written.
- 2026-10-05: T3 merged (`8a8cbc4`): review `review-82b031d38b733383` approved + burned, issue
  #248, report `../e11-briefs/e11-t3-rdd.md`.
- 2026-10-05: T4 merged (`465e1bd`): unit 2 + lane-combined reviews approved + burned
  (D-T4.1/D-T4.2 recorded), issue #250, report `../e11-briefs/e11-t4-rdd.md`; lane removed.
- 2026-10-05: T5 merged (`e04cde3` → `442ef91` with ODD record): 3 unit reviews approved +
  burned + `Refs #251` fix, issue #251, report `../e11-briefs/e11-t5-rdd.md`; lane removed.
- 2026-10-05: Wave 4 launched (T6 ‖ T7 ‖ T8): worktrees `../e11-t6|t7|t8` from `442ef91`,
  Herdr panes + OpenCode writers briefed with disjoint surfaces, DBs up, RDD procedure patched
  with D-T4.1/D-T4.2 + work-unit granularity (`a20dfbd`).

- 2026-10-05: T8 merged (`997a016`): review `review-727e456fcf63bb7d` approved + burned with
  0 findings, no follow-up issue, report `../e11-briefs/e11-t8-rdd.md`; gate post-merge green.
- 2026-10-05: T6 merged (`1765cf5`): review `review-a6aca8f6f1f43b9c` approved + burned with
  1 WARNING, issue #252, report `../e11-briefs/e11-t6-rdd.md`; gate post-merge 194 green; lane
  removed (worktree+rama+DB).
- 2026-10-05: T7 merged (`3f613ca`): review `review-fd40d9db212cbb50` approved + burned covering
  the whole lane (per-unit scoping corrected to one combined candidate), 1 WARNING refuted, no
  follow-up issue, report `../e11-briefs/e11-t7-rdd.md`; lane removed. Wave 4 closed (T6, T7, T8).
- 2026-10-05: follow-up lane pending for D-T7.1: two `metrics_*` views + `source_repository`
  methods to unlock `harvested_cycles_ratio` and `median_hours_to_first_reading` (migration +
  frozen-port change).
- 2026-10-05: `schema.d.ts` regenerated on the epic branch (`edf176b`) before forking T9, which is
  what made T9a ‖ T9b possible (neither lane touches the generated file).
- 2026-10-05: T9b merged (`228666d`): review `review-4b39d1bd807fbd23` approved + burned, 0 native
  findings; owner kept the `CACHE_BUSTER` bump over the reviewer's revert recommendation; F2 fixed
  in-lane (`8155512`); issue #255; report `../e11-briefs/e11-t9b-rdd.md`.

## Next step
T9b merged. T9a is finishing its impeccable craft-floor pass (its first run skipped
`reference/craft-floor.md` and the autonomy probe; owner rule is "impeccable sí o sí"), then a
`gate-lane` re-run over the final history and its own Pi RDD. After T9a: the D-T7.1 follow-up lane
(two views + source methods), then T10 close (`gate-release`, `field_record`/`relative_yield`
follow-up, delivery plan).
