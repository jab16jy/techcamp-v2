# TechCamp v2 — E10 Climate risk (M2 flood) rebuilt under the ML protocol

## Objective
Deliver epic E10 from `docs/10-dag.md:73`: model card, reproducible dataset, harness and the
baseline ladder for M2 (flood risk, municipality × month); production serves the model that passes
the gate or, if none passes, the baseline heuristic (ADR-0020). M3 (drought) follows M2 only if
the positive count allows it (docs/08 §Inventario). Serving: daily risk job (docs/06 §8),
`GET /plots/{plot_id}/risk` (docs/04 §Riesgo, métricas y asistente), and the `flood_risk` /
`drought_risk` alert rules (docs/06 §3, docs/03 seed table).

## Problem
Nothing exists: `server/src/techcamp/risk/` holds four empty `__init__.py` files; there is no
`ml/`, no CODEOWNERS, no `municipality` / `model_version` / `risk_prediction` schema, no
Open-Meteo archive client, and the seeded `flood_risk` / `drought_risk` rules have no evaluator
(`alerts/adapters/seed.py:79-101`). The v1 models cannot be rescued (ADR-0019).

## Why
RF-07 and RF-12 (docs/01). M2 is the only model planned before the pilot (docs/08 §Orden). E10
feeds E12 (assistant cites risk) and E15 (risk rules), off the critical path (docs/10).

## Scope
- In (`ml/`, own deps, ADR-0020 steps 1–10):
  - Model card first (`ml/models/flood_m2/card.md`).
  - Reproducible dataset from real public sources (owner, 2026-10-02: download authorized):
    Open-Meteo historical archive at municipality centroids, UNGRD / DesInventar flood events,
    elevation/slope; Caribbean municipalities; pinned versions, hash + data card.
  - Harness: temporal split with ≥ 6-month gap, department hold-out report, real class frequency
    in val/test, bootstrap CI95, locked test, `decide_promotion` without `force_promote`.
  - Baseline ladder → experiments log → calibration/threshold → one gate run → robustness report.
- In (`server/`):
  - Shared feature module `risk` (training/serving parity, docs/08 §M2 "Features").
  - Schema: `municipality`, `model_version`, `risk_prediction` (docs/03).
  - Daily risk job 06:00 (docs/06 §8): promoted model or baseline heuristic → `risk_prediction`
    with `model_version_id` and `top_factors`; Open-Meteo archive for the last 6 months per cell.
  - `GET /plots/{plot_id}/risk`, `POST /dev/jobs/risk:run`, org-isolation tests (docs/09).
  - `flood_risk` / `drought_risk` evaluator: severity ≥ `alto` → critical alert (docs/06 §3).
  - Model registration and manual promotion (docs/08 §Reglas de gobierno).
- Out:
  - "Riesgo climático" screen (owner, 2026-10-02): follow-up issue for a later epic.
  - M1, M4–M6 (docs/08: v2.x). `ndvi_point` (v1 data lost; not a declared M2 feature).
  - IDEAM river alerts (G07): documented as the model-card limitation, not built.

## Constraints
- Docs win (AGENTS.md). Decisions `D-T<task>.<n>`.
- Harness, locked test and gate (owner, 2026-10-02): a writer drafts them, the owner approves the
  slice before any experiment runs, then CODEOWNERS + a hash test (#192) freeze them. Experiment
  writers never touch them (AGENTS.md §Architecture rules, ADR-0020).
- Context7 (`find-docs`) before scikit-learn / LightGBM / TabPFN / Open-Meteo API use.
- TDD for every behavior change; targeted tests per task; `just gate-full` once at close.
- Artifacts to MinIO bucket `models` (ADR-0018, separate bucket; MinIO is in the seminar profile).
  The demo never downloads: datasets and artifacts are cached (ADR-0021).

## Decisions (T0)
- D-T0.1 Sources (research 2026-10-02, docs/08 §Fuentes de datos de M2): labels from the UNGRD
  datos.gov.co consolidated sets (`wwkg-r6te`, `rgre-6ak4`, `2343-nuqp`, 2019+); pre-2019 UNGRD
  yearly files or DesInventar only if they map cleanly, joined at a fixed cut-off year; DANE MGN
  2024 (`mpio_cdpmp`); Open-Meteo archive `models=era5` pinned (ERA5-Land has no precipitation);
  Open-Meteo elevation (GLO-90). CHIRPS is a cross-check only. TabPFN-2 weights only.
- D-T0.2 Region: 7 Caribbean departments, 195 municipalities (docs/08 §M2 "Región").
- D-T0.3 Horizon: month M predicted with data through the last day of M−1 (docs/08, docs/06 §8).
- D-T0.4 Severity `low|high|critical` from per-version `thresholds` (precision ≥ 0.7 / ≥ 0.85).
- D-T0.5 Baselines are `model_version` rows (`is_baseline`); the best validation baseline is
  served when no model passes the gate (docs/08, docs/03).
- D-T0.6 Serving reads the Open-Meteo archive directly (not `weather_daily`) for parity; recorded
  responses in the seminar profile (docs/06 §8).
- D-T0.7 `municipality`: all 1,122 from a versioned MGN CSV (code, name, department, centroid);
  `boundary` nullable (docs/03 §municipality…).
- D-T0.8 `ml/` layout and MinIO bucket `ml` (docs/08 §Estructura de `ml/`).
- D-T0.9 API shape of `GET /plots/{plot_id}/risk` (docs/04 §Riesgo, métricas y asistente).
- D-T0.10 Alerts open per plot of the cell at severity ≥ `high`, resolve on the first newer
  prediction below it (docs/06 §8).
- D-T3.1 Municipality point = DIVIPOLA cabecera (datos.gov.co `gdxc-w37w`), MGN layer 317 as the
  code/count check: the MGN service returns no geometry (owner, 2026-10-02; docs/08 §Fuentes).

### Decisions (T6b)
- D-T6b.1 Serving builds the M2 features **without** a climatology, so `precip_anomaly_*` is `None`
  (missing evidence, never `0`). No doc says where a served version's train years live, and T6b does
  not invent a `metrics` key for it: the daily job asks the archive for the six months before M and
  builds the rest of the vector from them. T9 owns the question — it registers the real predictors
  (docs/08 §M2 "Features": anomalies against the *train* climatology) and wires the climatology with
  them. Owner, 2026-10-02.
- D-T6b.2 The daily risk job is **one** procrastinate task at 06:00 America/Bogota that walks the
  cells with plots in a loop, not a fan-out of one job per cell like `weather`/`irrigation`: the
  endpoint docs/04 §Solo perfil seminario answers is a single `{ job_id }`, and an archive outage
  skips its cell and the run goes on (docs/06 §8). Owner, 2026-10-02.

## Tasks
Forecasts are authored lines (additions + deletions, generated excluded). Route = writer and reason.

- [x] T0 Decisions into owner docs: docs/08, docs/06 §8, docs/03, docs/04, glossary (no ADR:
  no accepted decision changed). ~150, actual ~70. Route: parent inline (planning is never
  delegated; the `domain-modeling` skill is not installed, so the existing doc conventions were
  followed).
- [x] T1 `ml/` scaffold: uv project depending on the server package (feature parity), layout of
  ADR-0020 (`models/`, `datasets/`, `harness/`, `experiments/`), M2 model card (step 1),
  CODEOWNERS and the hash-lock test framework (#192). ~250. Route: Herdr writer.
- [x] T2 Shared features (`risk` domain, pure): rainfall accumulations 1–6 months, anomalies vs a
  train-only climatology, seasonality, elevation/slope inputs; hand-computed rainfall and terrain examples as tests.
  ~300. Route: Herdr writer. Parallel with T1/T3 (no shared files).
- [ ] T3 Dataset sources: municipality reference (DIVIPOLA + centroids, Caribbean), Open-Meteo
  archive downloader, flood-event labels (UNGRD / DesInventar), elevation; pinned versions, cache,
  data card. ~400. Route: Herdr writer (network authorized). Depends T1.
- [ ] T4 Dataset build: municipality × month table through T2 features, all negatives, hash.
  ~200. Route: Herdr writer. Depends T2, T3.
- [ ] T5 Harness + gate (owner approval gate): split with gap, department hold-out, real frequency,
  bootstrap CI95, locked test, `decide_promotion`; own tests; freeze hashes after approval.
  ~400. Route: Herdr writer. Depends T1 (schema from T4 for wiring).
- [~] T6 Server schema + risk serving (T6a done `515f399`; T6b next): migrations (`municipality`, `model_version`,
  `risk_prediction`), baseline heuristics (flood: accumulated rainfall; drought: SPI-3), archive
  client, daily job, `/dev/jobs/risk:run`, `GET /plots/{plot_id}/risk`. ~400. Route: Herdr writer.
  Depends T2. Parallel with T3–T5.
- [ ] T7 Alerts: `flood_risk` / `drought_risk` evaluator from `risk_prediction` (pattern:
  `fungal_risk` sweep). ~200. Route: Herdr writer. Depends T6.
- [ ] T8 Experiments (steps 4–9): ladder, tuning, calibration/threshold, one gate run, robustness.
  ~400. Route: Herdr writer. Depends T4, T5 approved.
- [ ] T9 Registration + promotion (step 10): artifact to MinIO, `model_version` row, serving loads
  the promoted model or keeps the baseline. ~250. Route: Herdr writer. Depends T6, T8.
- [ ] T10 Close: `just gate-full`, demo run, follow-up issues (risk screen), delivery plan. Route:
  parent.

## Lanes
Wave 1 (after T0): T1, T2. Wave 2: T3, T6 (after T2). Wave 3: T4, T5, T7. Wave 4: T8. Wave 5: T9.

## Review (RDD)
Branch point: `a2bae6b` (main). RDD on (global). Per work-unit commit: `gentle-ai review assess`.
- T0 `06ca02f`: assess passive; START closed `approved` with no lenses (low, non-executable only),
  lineage `review-1226f740382b58ff`, acknowledged. Reviewed boundary → `06ca02f`.
- T2 `99839c5` (lane `e10-t2`): the Pi writer ran RDD itself (out of brief); lineage
  `review-2c38c84a60f334ec` approved, reliability lens, 4 non-blocking findings (ids/locations
  only in the receipt) → #237. R3-004 and R3-001 fixed in the lane (`Refs #237`); R3-003 fixed
  here; R3-002 open.
- Epic branch vs main after T2 (lineage `review-6e36d3054a66495d`): approved, reliability lens,
  3 WARNINGs → #238, all fixed in `0bdf632` (window < 1 month raises, NaN is missing, year
  boundary test). Later whole-branch candidate (22 files incl. T1): owner declined.
- T1 (AGY, no RDD of its own; parent ran it): first START `lens_context_budget_exceeded`
  (`ml/uv.lock` ~2.8k lines). Review slice `e10-t1-review` = lock commit `50209bf` + the rest
  (tree identical to `e10-t1`); lineage `review-360ab4b7b68b05db`, 4 lenses, one CRITICAL
  `R4-ml-lockfile-missing` (inferential: the lockfile sat in the slice's base, unseen). Owner
  chose the bounded fix `--locked` on every ml `uv run` (`3b21ab6`, cherry-picked as `d35f0a4`,
  gate green). The validator, also blind to the base, rejected it → `escalated`,
  `native_stop_required`: T1 has no approved receipt. Lesson: never split a generated lockfile
  into the review base; exclude it from the candidate some other way or review the code first.
- T6a (Pi; `1c7bbe6`, `6d6fb03`, `aac5808`): the Pi host's capture tool rejected its own STATUS
  bindings (`capture-binding-rejected`, 4× on lineages `review-59ef866551e0b37c`,
  `review-4d3db5cb0650f1b4`); owner: continue without reporting. Parent ran it natively once
  (lineage `review-6cbfe58d6e9462b1`, reliability): CRITICAL `R3-served-version-ignores-promoted`
  fixed in the bounded correction `85d528e` (validator passed) → approved, acknowledged; WARNING
  `R3-json-decode-escapes-contract` → #239, fixed `a45ec55` (unreviewed: Pi capture defect).
  Merged `515f399`. Owner rules from this round: RDD is the Pi writer's own job; work units
  ≤ ~400 lines, lockfiles alone.

## Delivery
Strategy: `ask-on-risk` resolved by AGENTS.md → stacked-to-main chained PRs of ~400 lines.
Forecast ≈ 2950 authored lines.

## Progress / evidence
- 2026-10-02: mapping done (risk empty, no ml/, issue #192 only). Owner decisions: real-source
  download authorized; harness drafted by a writer and approved by the owner; risk screen out.

## Next step
T0.
