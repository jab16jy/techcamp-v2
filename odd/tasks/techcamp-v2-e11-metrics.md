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

## Progress
- 2026-10-02: worktree `../techcamp-v2-worktrees/e11-metrics` (`feat/e11-metrics` from
  `origin/main` a2bae6b), CodeGraph index, DB `techcamp-db-e11-metrics`. Explorers (Herdr
  OpenCode docs map, AGY lessons) → `../techcamp-v2-worktrees/e11-briefs/*.md.out`. T0 written.

## Next step
T2 merged. T3 (`e11-t3`) gate passed; its RDD (`review-9b9197b7eee80109`) is relaunching the re-offered slot after an admission rejection. Then wave 3: T4 ‖ T5 ‖ T9a.
