# TechCamp v2 — Field reality fixes to the system design

## Objective
Apply the design changes justified by `docs/investigacion/tecnificacion-campo.md` so the design is implementation-ready: correct agronomy (water stress, sensor assimilation), support irrigated **and** rainfed plots, make impact metrics computable, and close cheap evidence gaps.

## Problem / why
The research (gap matrix G01–G28, validated by the parent) found blockers: fixed per-crop moisture threshold contradicts FAO-56 (G01, G02), a 10 cm sensor replaces the root-zone balance (G03), metrics need fields that do not exist (G04). Only 33.3% of UPA with crops irrigate (CNA 2014), so the product must serve rainfed plots (G06). "Línea base" has two meanings in the glossary.

## Decisions (owner)
- 2026-09-22: the seminar and a future pilot target **both** irrigated and rainfed plots.
- Accepted order from the parent proposal: blockers → cheap renames/notes → rainfed mode → extension visit. Pilot-only gaps stay out.

## Scope
- In: G01, G02, G03, G04, G05, G06, G07 (model-card note only), G08, G09, G10, G11, G12, G15, G18; glossary split of "línea base".
- Out: G13, G14, G16, G17, G19–G28 (pilot or agronomist-dependent; they stay listed in the research report). Open owner questions: MADR 1–4 classification alignment of the index, who pays/maintains nodes, data ownership and foreign providers, sex/age disaggregation, riverine floods beyond the M2 note.

## Constraints
- Neutral professional Spanish docs; English identifiers; Mermaid diagrams.
- Each changed decision cites its gap ID and the research doc (the "why").
- Agronomic parameters that need local validation are marked as pending agronomist review, not invented.

## Route and checks
- TDD: off (docs only, no test runner).
- Route: delegated direct, one writer on Opus 5.5 (user request). Writer trigger fired (10+ files); mapping is inside the writer's preparation reading.
- Checks per task: link/anchor checker (0 broken); Mermaid parse of changed blocks; consistency greps (no stale `stress_threshold_pct`, `technification_index`); scenario arithmetic shown.
- Delivery strategy: exception-ok (docs-only precedent: PR #1 approved as `size:exception`). Forecast ~500 authored lines. Branch `docs/field-reality-check` (stacked on `docs/seminar-alignment`).

## Tasks
- [x] T1 Water stress and sensor assimilation (G01, G02, G03, G18) + ADR-0022 — route: delegated (writer 1) — `3dba412`
- [x] T2 Irrigated and rainfed plots (G06, `irrigation_system`) + ADR-0023 — route: delegated (writer 2) — `8fea9e5`
- [x] T3 Impact data and metrics (G04, G05, G09, G10, G11, "línea base" split) + ADR-0024 — route: delegated (writer 2) — `f2280b5`
- [x] T4 Cheap evidence notes (G07 model card, G08 ENSO/MTA, G12 AU915) — route: delegated (writer 2) — `f89d6b4`
- [x] T5 Extension visit (G15) — route: delegated (writer 2) — `e41ebad`
- [x] T6 Parent validation: close remaining metric-field gaps and fix the research clay values — route: inline (mechanical, 3 files)

## Acceptance criteria
- No fixed moisture threshold per crop remains; water stress is defined per plot from soil + crop stage, consistent across glossary, 03, 06, 11 and the scenarios.
- Scenario arithmetic in 06 §10 matches FAO-56 with the stated soil parameters.
- Every metric in 11 maps to fields that exist in 03.
- Rainfed plots have a defined recommendation, UI card variant, metrics treatment and a simulator scenario.
- ADRs 0022–0024 exist, cite the research gap IDs, and are indexed; ADR-0009 points to 0022.
- 0 broken links; changed Mermaid blocks parse.

## Progress / evidence
- `c2b0fc3` research report added to `docs/investigacion/` and indexed (review assess: passive).
- Writers: the first writer completed T1 and was stopped; a second writer did T2–T5. The scratchpad `linkcheck.py`/`parse.mjs` were missing, so writer 2 recreated them (mermaid + jsdom in the scratchpad).
- `3dba412` T1 (writer 1, delegated): per-plot `Dr > RAW`, weighted assimilation `K`, `kc_source`, ADR-0022, recomputed scenario A. Checks re-run by writer 2 at the end (below).
- `8fea9e5` T2 (delegated): `plot.irrigation_system`/`irrigation_efficiency`, `irrigation_recommendation.kind`/`advice`, rainfed branch and advice table in 06 §5, rainfed card, scenario E with arithmetic, WUE irrigated-only, index re-weighting, ADR-0023. Links 0 broken; changed Mermaid parsed.
- `f2280b5` T3 (delegated): `sold_kg`, `sale_price_cop_per_kg`, `labor_days`, `alert_id`, `plot_baseline`, `calibration.rmse_pct`, `relative_yield`, `digital_adoption_index`, action-based `risk_management`, G09/G10 metric rows, ADR-0024. Links 0 broken; Mermaid parsed.
- `f89d6b4` T4 (delegated): M2 model-card limitation (IDEAM rule baseline), ENSO candidate feature for M3, seasonal-climate assistant fact, bulletins in the RAG corpus (`kb_document.kind/published_on/department/enso_state`), AU915 in 02, ADR-0004 and docs/README. Links 0 broken; Mermaid parsed.
- `e41ebad` T5 (delegated): `extension_visit` (Ley 1876 five-aspect `topics`, offline sync), `farm.technician_id`, API, technician tray (07), RF-19, E8/E9 in the DAG. Links 0 broken; Mermaid parsed.
- Final checks (writer 2, after T5): linkcheck `broken: 0` (40 files); Mermaid 19/19 blocks parse in every file changed since `c2b0fc3`; greps outside `docs/investigacion/`: `stress_threshold_pct` 0, `technification_index` 0, "Brecha de rendimiento" 0, old assimilation "reemplaza" wording 0 (only ADR-0022 context/alternatives and the 06 negation remain). `git diff --stat c2b0fc3..HEAD`: 19 files, +528/−114 before this progress commit.
- Review assessment (RDD): not run by the writer; left to the parent.

## Decisions made during implementation (writer 2)
- Scenario E uses sandy loam (θFC 0,23 / θWP 0,09), the only texture with consistent values across the research, ADR-0022 and scenario A. "Clay-loam" is not in FAO-56 Table 19, and the research's clay values (θWP ≈ 0,27) contradict ADR-0022 (Table 19 θWP 0,20–0,24). Zr = 1,0 m at flowering (lower FAO-56 bound) marked pending validation.
- Rainfed status value `stress` (API, token `--color-status-stress`) instead of reusing `irrigate`.
- Rainfed advice rules: `delay_sowing` (no active cycle; P7d < ET0 7d), `rain_expected`, `conserve_moisture`, `prioritize_harvest` (late stage), `no_action`; 7-day forecast horizon; push only when advice changes. All pending agronomic validation. The daily job now also visits rainfed plots without an active cycle for `delay_sowing`.
- `alert_id` allowed on any logbook kind (action evidence for `risk_management`); losses = `observation` with `alert_id` using `quantity`+`unit` (kg) and `cost_cop` (COP), excluded from `Σ costos`.
- `risk_management` window: action within 48 h of opening; node alerts excluded; irrigation entry counts for `water_stress` on irrigated plots.
- `relative_yield` = yield / 3-year EVA median (was a difference against the mean), consistent with the research and 08 M6.
- Anticipation target kept at median ≥ 24 h for forecast rules only; M2/M3 reported without target.
- Added `calibration.rmse_pct` (for G10), `kb_document.kind/published_on/department/enso_state` (for G08) and `farm.technician_id` (the design already referred to an "assigned technician" without a field).
- ADR-0008 left unchanged; the corpus sources are described in 06 §9.

## Remaining gaps (metrics without fields) — closed in T6
- `relative_yield`: `field_record` (EVA) has no column schema in 03.
- "Agua aplicada ... o caudalímetro": no flow-meter metric in `sensor.metric`.
- `monitoring` and "tiempo a primera lectura": no `node.interval_s` or claim timestamp.
- "Precisión de alertas": no field for a confirmed event; M2/M3 anticipation needs event labels stored outside training.
- "Uso offline": no flag for entries created offline.
- Cost per kg with valued family labour needs a reference wage (not modelled).

## Parent validation (T6)
- Re-derived scenario A (θ_estrés 15,3 %, TAW 84, RAW 46,2, crossing day ≈ 10,4, Dr_obs 60 > RAW) and scenario E (TAW 140, RAW 77, stress opens day 16, Dr 105 on day 21): correct.
- Agreed with writer 2's sandy-loam choice for scenario E: the research used clay θWP ≈ 0,27, outside FAO-56 Table 19 (0,20–0,24). Research line corrected with a note; conclusion unchanged.
- Closed metric gaps in 03/00: `node.interval_s`, `node.claimed_at`, `alert.outcome`, `logbook_entry.created_offline`, `water_flow` metric, `field_record` column list. The "reference wage" gap needs no field: no metric in 11 values family labour.
- Checks: linkcheck `broken: 0` (40 files); Mermaid 03 1/1 OK; grep `stress_threshold_pct`/`technification_index` 0 outside the research.

## Next step
User review; push/PR is the user's decision. Open owner decisions: MADR 1–4 index alignment, node payer/maintainer, data ownership and foreign providers, sex/age disaggregation, riverine floods.
