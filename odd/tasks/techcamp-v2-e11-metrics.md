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
  [11-metricas §2](../docs/11-metricas.md) and [ADR-0024](../docs/adr/0024-metricas-de-impacto-y-adopcion-digital.md);
  the SQL is unchanged.
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
- [ ] T1 Schema: migration for `plot_baseline`, `plot_metric_monthly`, `crop_cycle_summary`
  (org_id, FKs, CHECKs), SQLAlchemy tables, import-linter arrows (D-T0.1). ~200. Route: Herdr
  OpenCode. Depends T0.
- [ ] T2 Enrollment survey: `GET/PUT /plots/{plot_id}/baseline`, use cases, repository, roles,
  isolation tests. ~350. Route: Herdr OpenCode. Depends T1. Parallel with T3.
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
reviewed by the parent. Findings rule (owner, 2026-09-30): blocking → bounded correction; 1–2
non-blocking → one issue per round; 3+ → fix the most important with `Refs #N`, file the rest.

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
[ADR-0024](../docs/adr/0024-metricas-de-impacto-y-adopcion-digital.md); **el predicado no cambió**
(solo el docstring de la vista). Commit de doc: `823974d`; commit de pruebas: `a1941ba`.

Commits de T3, en orden: `7cc3dd7` (port), `51052a1` (builders), `09cef3f` (monitoring),
`407ed2b` (record-keeping + decision), `230289c` (alert action), `429b293` (cycle totals),
`371c0e7` (corrección acotada), más los dos de cierre.

Los 10 hallazgos no bloqueantes **no** se corrigieron aquí: el cuerpo del issue queda redactado abajo
y su creación es del orquestador. Nota sobre el conteo: el revisor emitió 12 hallazgos y el refutador
dejó 1 en `fix_finding_ids`; de los 11 restantes, 4 quedaron cubiertos por el commit `a1941ba` y 7
siguen vivos (5 sin base en el modelo + `water-balance-date` y la cota del mes en curso).

#### Issue propuesto (no abierto) — `review-follow-up`, `epic:e11`, `area:metrics`, `type:tech-debt`

**Título:** E11 T3: diez hallazgos no bloqueantes de la revisión del lineage `review-9b9197b7eee80109`

**Cuerpo:**

RDD de las vistas de solo lectura de `metrics` (T3), lineage `review-9b9197b7eee80109`, terminó
escalado sin recibo. El revisor emitió 12 hallazgos y el refutador dejó 1 en `fix_finding_ids`: el
CRITICAL bloqueante `R3-reliability.alert-action.cross-plot`, que se descargó con el ruling del dueño
**D-T3.1** (ver §Review de esta feature doc) haciendo explícito el doc que el SQL ya cumplía. Este
issue recoge los once restantes, que no se corrigieron aquí porque la regla de findings deja la
decisión al orquestador. De ellos, cuatro quedaron cubiertos por el commit `a1941ba` y siete siguen
vivos.

**Hallazgos refutados o sin base en el modelo (5)** — conviene registrarlos para que la próxima
revisión no los levante otra vez:

| id | Por qué no es un defecto |
|---|---|
| `R3-reliability.decision-view.duplicate-days` | `uq_irrigation_recommendation_plot_day` hace `(plot_id, day)` único; el empate que el `ORDER BY` tendría que romper no existe. CHECK probado en `test_decision_views.py` |
| `R3-reliability.cycle-totals.null-sale-price` | `ck_logbook_entry_sold_and_price` exige `sold_kg` y `sale_price_cop_per_kg` juntos; la rama de propagación nula es inalcanzable. CHECK probado en `test_cycle_totals_view.py` |
| `R3-reliability.alert-action.rule-window` | La ventana de 48 h es una constante de todo el componente por D-T0.6, y `alert_rule` no tiene columna de ventana de acción (`min_duration_min` gobierna cuánto debe durar una violación, no cuánto tiene el productor para responder) |
| `R3-reliability.alert-action.entry-scope` | docs/03:421 asigna el papel a `alert_id` en *cualquiera* entrada; el filtro por `kind` contradiría el doc |
| `R3-reliability.alert-action.repeat-entry` | La rama por `alert_id` responde exactamente la alerta que nombra; solo la cláusula de riego plot-scoped puede servir dos, y `uq_alert_non_resolved_plot` permite una alerta no resuelta por (regla, parcela) |

Los cinco están registrados como comentario en `server/tests/metrics/test_alert_action_view.py` y con
un CHECK probado en los otros casos, para que la próxima revisión no los levante otra vez.

**Hallazgos reales que siguen abiertos (2)**:

| id | Qué falta |
|---|---|
| `R3-reliability.cycle-totals.water-balance-date` | Dos ciclos de una misma parcela con ventanas solapadas se reparten los mismos días de balance y se cuenta un día de estrés dos veces. Como `expected_harvest_on` es un plan que suele pasar de la siguiente siembra, el solapamiento es la norma. **No se corrigió a propósito:** es el síntoma del hueco de doc de `crop_cycle` sin fecha de fin; va junto con esa decisión de docs, no suelto. Afecta a `crop_cycle_summary.water_stress_days`, que T4 persiste |
| `R3-reliability.monitoring.now-dependence` | Rama del mes en curso. **Mitigada** con una aserción de cota en `a1941ba` (el `now()` vive en SQL y no se puede congelar sin un seam de reloj); falta ese seam para un valor exacto |

**Hallazgos reales, cerrados en `a1941ba` (4)** — los cuatro de Tier 1 que sí descongestelan T4/T5:
dos sensores en un mismo nodo (`monitoring.duplicate-count`), la rama `COALESCE` del ciclo activo
(`cycle-totals.now-dependence`), el aislamiento por organización del lateral de balance
(`cycle-totals.plot-isolation`) y la parcela sin recomendaciones (`decision-view.no-evidence`).

## Progress
- 2026-10-02: worktree `../techcamp-v2-worktrees/e11-metrics` (`feat/e11-metrics` from
  `origin/main` a2bae6b), CodeGraph index, DB `techcamp-db-e11-metrics`. Explorers (Herdr
  OpenCode docs map, AGY lessons) → `../techcamp-v2-worktrees/e11-briefs/*.md.out`. T0 written.

## Next step
T1 brief and Herdr OpenCode writer.
