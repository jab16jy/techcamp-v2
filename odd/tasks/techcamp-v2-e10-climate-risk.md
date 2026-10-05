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
- D-T3.1 Municipality point is the DIVIPOLA municipal seat, with MGN 2024 layer 317 as the
  code control (layer answers `geometry: null`, so there is no centroid); docs/08
  §Fuentes de datos de M2 updated in the same work unit (owner, 2026-10-02).
- D-T3.2 Labels stop at 2019 (owner, 2026-10-02): UNGRD 2019-2025 only, no DesInventar and
  no pre-2019 consolidated, no source mixed inside a year. 1,508 events over 180 of the 195
  municipalities. Documented in `ml/datasets/flood_m2/data_card.md`.
- D-T3.3 The missing ERA5 archive is completed through the **free** Open-Meteo archive, one `fetch`
  per day as the daily quota resets (owner, 2026-10-03). Rejected: the commercial API key (paid),
  Copernicus CDS directly (breaks docs/08 §M2 "Features": same source as serving), a self-hosted
  Open-Meteo (disproportionate), and any quota evasion (terms of use). The free tier is 10,000
  weighted calls/day per IP; a call weighs by range length, locations, variables and models
  (open-meteo.com/en/docs, via ctx7). Missing on 2026-10-03: `archive_003` (95 codes,
  2022-06-30..2026-06-28, ~one day of quota) and `archive_004/005` (195 codes, 2026-06-29..last
  complete month, small) — about 26% of the municipality-days, an estimated 2 days of fetch.

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

- D-T5.1 The harness lock `ml/harness/LOCK.sha256` is **re-frozen** (owner, 2026-10-05). It pins
  the five `ml/src/techcamp_ml/harness/**` files, so the two blocking CRITICALs in `promotion.py`
  were unfixable inside a bounded correction without it. Re-freezing changes what "approved and
  frozen" means for the rest of E10, so it is recorded as a decision rather than a mechanical step.
  `F3` (#249) stayed outside that correction even though the lock was being re-frozen anyway: adding
  a third finding to a bounded correction destroys the discipline that makes it bounded.

## Tasks
Forecasts are authored lines (additions + deletions, generated excluded). Route = writer and reason.

- [x] T0 Decisions into owner docs: docs/08, docs/06 §8, docs/03, docs/04, glossary (no ADR:
  no accepted decision changed). ~150, actual ~70. Route: parent inline (planning is never
  delegated; the `domain-modeling` skill is not installed, so the existing doc conventions were
  followed).
- [x] T1 `ml/` scaffold: uv project depending on the server package (feature parity), layout of
  ADR-0020 (`models/`, `datasets/`, `harness/`, `experiments/`), M2 model card (step 1),
  CODEOWNERS and the hash-lock test framework (#192). ~250 forecast, **~560 actual** (2.2x;
  3376 raw of which 2816 is the generated `ml/uv.lock`). Route: Herdr writer.
- [x] T2 Shared features (`risk` domain, pure): rainfall accumulations 1–6 months, anomalies vs a
  train-only climatology, seasonality, elevation/slope inputs; hand-computed rainfall and terrain examples as tests.
  ~300 forecast, **677 actual** (251 prod + 418 test, test/prod 1.67). Route: Herdr writer.
  Parallel with T1/T3 (no shared files).
- [~] T3 Dataset sources: municipality reference (DIVIPOLA seat, Caribbean), Open-Meteo archive
  downloader, UNGRD flood-event labels (D-T3.2), elevation; cache with provenance manifest, archive
  plan, data card. ~400 forecast, **2488 actual** for the first round (6.2x — see
  [Calibration](#calibration)) plus the #241 round (7 commits). **Code done and merged**
  (`2794932`, 66 ml tests green): reviews `review-07c50aaba6995614` (lane `eeaa5bd..3f60153`,
  approved, follow-up #241), `review-592d021505ff5e3b` (`3f60153..74bc8c6`, escalated; parent
  triage in #241: one real finding, fixed in `d640fcf`), `review-43121bc6a8eb042f`
  (`3f60153..d640fcf`, approved, 4 advisory WARNINGs in #241; the partial-labels one goes to T4).
  **Data partial** (D-T3.3): ERA5 archive at 3 of 6 chunks; the cache lives in worktree
  `e10-t3` (`ml/.cache/raw`, gitignored) — do not remove that worktree before the cache is moved.
  `parse` refuses the current cache (no `archive_plan.json`); the next real `fetch` writes the plan.
  Route: Herdr writers (Pi, then OpenCode space-bunny high). Depends T1.
- [x] T4 Dataset build: municipality × month table through T2 features, all negatives, hash.
  ~200. Route: Herdr writer. Depends T2, T3. Merged `fefeb00`, receipt `review-4b11cb9d8f8faa8c`
  (4 lenses). Built and tested on fixtures; the real build is unblocked but not run (D-T3.3).
- [ ] T5 Harness + gate (owner approval gate): split with gap, department hold-out, real frequency,
  bootstrap CI95, locked test, `decide_promotion`; own tests; freeze hashes after approval.
  ~400. Route: Herdr writer. Depends T1 (schema from T4 for wiring).
- [~] T6 Server schema + risk serving (T6a done `515f399`; T6b next): migrations (`municipality`, `model_version`,
  `risk_prediction`), baseline heuristics (flood: accumulated rainfall; drought: SPI-3), archive
  client, daily job, `/dev/jobs/risk:run`, `GET /plots/{plot_id}/risk`. ~400. Route: Herdr writer.
  Depends T2. Parallel with T3–T5.
- [x] T7 Alerts: `flood_risk` / `drought_risk` evaluator from `risk_prediction` (pattern:
  `fungal_risk` sweep). ~200. Route: Herdr writer. Depends T6. Landed `e6f3060` (9 commits on
  `cd270ad`), receipt `review-c73397a8bb8c3785` + the round-3 approval of `cd48e0d`. Two CRITICALs
  found by the parent gate and by the round-2 review on the way; see §Review (RDD).
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
- T4 (Pi; `ff92197`, `9581e02`, `5fac554`, `4eae2f3`, lane `e10-t4`): lineage
  `review-4b11cb9d8f8faa8c` over `2794932..5fac554`, 4 lenses, APPROVED and acknowledged. 14
  non-blocking findings → #245; the four WARNINGs (manifest published **before** the dataset,
  outputs that follow the given layout, neighbour axes read by name, month range from
  `horizon_start`) plus the duplicate `(code, date)` refusal fixed in `4eae2f3`, with the data card
  updated in the same unit. Merged `fefeb00`. `4eae2f3` (321 authored lines) is unreviewed by
  budget, not by omission: `review_due: false`, `under_budget`.
- T7 (Pi; 9 commits on `cd270ad`, lane `e10-t7`): round 1 lineage `review-c73397a8bb8c3785` over
  `cd270ad..1f247b7`, reliability lens, APPROVED and acknowledged; 2 WARNING + 1 SUGGESTION →
  #246, both WARNINGs fixed in `db967d6`.
  **The parent gate found a CRITICAL in that fix round.** `db967d6` added a branch to
  `get_decided_for_target` — the alert that was open when the prediction was issued absorbed it —
  and `evaluate_risk_rules` skipped the decided-record lookup *before* deciding. Production stores
  predictions at 06:00 and evaluates minutes later, so the resolving LOW row of the next month is
  always issued while the alert is still open, matched that record, and was skipped:
  `flood_risk`/`drought_risk` could never resolve, against docs/06 §8. The lane's own test passed
  only because its fixture placed `issued_at` BEFORE the alert's `opened_at`, an ordering production
  never produces. Fixed in `ae735b8` by deciding first and letting the record suppress OPEN and
  NO_ACTION only.
  Round 2, lineage `review-ebf79c2f6891e708` over `1f247b7..ae735b8`, ended **escalated**,
  `unknown_causality`: the lens was admitted and raised `R3-replayed-resolution-bypasses-decided-check`
  (CRITICAL), downgraded at admission on `unverified_location` → **#247**, owner-filed.
  **That CRITICAL was introduced by the parent's own brief.** Suppressing nothing on RESOLVE meant
  that branch never consulted the record at all, so a stored prediction that had already resolved
  one alert could replay and resolve a different open one. Fixed in `cd48e0d` by consulting the
  record on EVERY action and excluding the currently open alert from the match — a better shape
  than the parent prescribed, and the second mirror of the same bug. Round 3 over
  `1f247b7..cd48e0d` APPROVED and acknowledged. Merged `e6f3060`.
  Round 2's escalation approves nothing, burned no authority, and does not block the merge.
- T5 (Pi; `e73867c`, `6b093cd`, `7fc6498`, `376ac14`, lane `e10-t5`): lineage
  `review-6cd12d59a63983e9` over `fefeb00..376ac14`, medium, 9 paths / 1237 lines, reliability
  lens. Two blocking CRITICALs, both in `promotion.py`, both `introduced` and `deterministic`:
  `R3-test-read-repeatable` — the one-read gate kept no consumed state, so a second
  `decide_promotion` rescored the blocked test, and the existing call-count assertion could only
  prove one pass within ONE invocation; and `R3-incomplete-test-accepted` — any nonempty fragment
  of the labelled year passed as the complete test block, and two rows (one positive, one negative)
  are enough for a candidate to win the paired interval and the Brier, so `promote` was reachable
  from an arbitrarily incomplete test set. That is the exact failure ADR-0020 exists to prevent.
  Non-blocking `R3-validation-boundary-bypass` (`val_first` can move or empty the validation block
  while passing the gap checks) → **#249**, deliberately outside the bounded correction.
  Owner re-froze the harness lock (D-T5.1). The first correction closed F2 — `_require_complete_year`
  demands all twelve months — and only half of F1; reviewing the correction, the reviewer found the
  guard keyed on `(id(table), id(candidate))`, which an equivalent copy of the table defeats by
  construction, so the invariant cannot rest on object identity → **escalated**
  (`review-b34713697b28f808`, `unknown_causality`). Separate dead end: the correction plan for
  `review-6cd12d59a63983e9` was unadmittable because the facade cannot see base-diff lineages.
  **T5 has no approved receipt.** Correction in flight.

### Six dead ends in the RDD lifecycle, so they stay dead

Every one of these cost a full round on T5 or T7. Each is stated with the evidence that
distinguished it from the next one, because that is the part that is expensive to rediscover.

1. **`capture-binding-rejected` is the binding SHAPE, not the runtime.** The facade emits
   `collectBindings` in camelCase from `gentle_review {status}`; passing the CLI-shaped binding is
   refused. The capture also needs `reviewerRunAcknowledged: true` to close into
   `native-last-event-closure`. Cost three rounds.
2. **`--agent` takes a RUNTIME identity, not a model or a provider.** `claude-code`, `codex`, `pi`.
   `opencode` is a provider name and is rejected outright with `unassessable`.
3. **Pi is eligible only inside the gentle-pi host.** `--agent pi` works only while
   `GENTLE_PI_REVIEW_RELAY_CONTRACT=gentle-pi.review-relay/v1` is exported, which the host does on
   every invocation it relays. From any other shell the assessment returns `unassessable`, and the
   `high_risk` it then reports is the runtime declining, not the candidate's tier.
4. **Three refusal layers with different storage.** `output_refused` is admission-stage, AFTER a
   reviewer ran, and stores its output under `<common-git-dir>/gentle-ai/rejected-results/<lineage>/`.
   `binding_mismatch` is at the relay and stores nothing. Binding validation is pre-dispatch and
   stores nothing. The absence of a `rejected-results` directory is evidence, not silence.
5. **A new target does NOT give an independent lineage.** Target identity is a deterministic
   function of `(base_tree, candidate_tree, paths_digest, intended_untracked_proof)` — not of the
   attempt number. The same bounded assess from the same worktree reproduces it byte for byte and Go
   re-binds to the existing lineage.
6. **The facade cannot see base-diff lineages for a correction plan.** It admits them for the
   review itself and then refuses the plan.

Two more that are not refusals but cost the same: the relay process is spawned with
`ctx.location.directory`, the SESSION's directory, not the lifecycle `--cwd`, so a shell `cd` cannot
fix a binding that does not match; and the selectorless `review status` binds the merge-base with
`main` and freezes the whole epic as the candidate, which burned E11 T1 and E11 T3's first attempt.
Use `assess --base-ref <boundary> --committed-only`, then the transition it returns.

## Calibration

Measured 2026-10-02 over `e10-t3` while closing T3. Line counts are additions + deletions with
generated files excluded, per the ODD heuristic.

| Task | Forecast | Actual | Overrun | prod | test | test/prod |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| T1 | ~250 | ~560 | 2.2x | — | — | — |
| T2 | ~300 | 677 | 2.25x | 251 | 418 | 1.67 |
| T3 | ~400 | 2488 | 6.2x | 1132 | 1119 | 0.99 |

Three findings, in order of how much they cost:

1. **Line forecasts in this epic under-count by ~2.2x as a baseline.** T1 and T2 overran at 2.2x
   with no failures at all — tests simply were not in the number, and here they weigh as much as
   production code (1.67:1 in T2). A ~400-line budget under a TDD mandate is ~200 lines of
   production code; `pipeline.py` alone is 353. The budget and the TDD rule are arithmetically
   incompatible as written.
2. **T3's forecast did not scale with its scope.** It got ~400 (33% more than T2) for 7
   deliverables against T2's ~2. Even with a perfect forecast the 6 `feat` commits alone are 1934
   lines — 4.8x. The 641 lines of rework (25%) are real but are not the main cause.
3. **The rework has one root cause, not four.** All four `fix` commits and three `docs` commits in
   T3 are the Open-Meteo client: `3f42506` (over-spending the call budget), `f63c726` (one-location
   response body, ERA5 lag, "stop guessing quota"), `2adc269` (a half-finished download read as
   finished), `a3cb340` (region resume). The writer never obtained a verified contract — the first
   request hit the 429 — so it wrote against assumptions and each one surfaced separately.

### Owner rules from this round

- **Capture one real response as a fixture before writing any external HTTP client, in the first
  commit of the integration.** That fixture is the contract; the client is written and TDD'd against
  it. This is what turns four fix rounds into zero.
- **Forecast in deliverables, not lines.** "7 deliverables, 4 sources, N tests" is a forecast that
  can be checked; "~400 lines" cannot. Let the lines fall out after.
- **TDD is repo-wide from E2 and is not negotiable per brief.** `21bf9c7` shipped 432 lines of
  `pipeline.py` / `cli.py` / `__main__.py` with no test; `test_pipeline.py` arrived in `a3cb340`
  and `2adc269`. That is the violation to fix, not the size.
- **Report the overrun when it crosses the forecast, do not grind past it.** T3 passed 2x forecast
  by commit `0067785` and the writer said nothing.

Not changed here: AGENTS.md still states ~400 authored lines per chained PR, and the ODD heuristic
still reads ~400 per task. Both are stale against the table above. Recalibrating AGENTS.md is a
project-level decision outside this feature doc's scope — owner call.

## Delivery
Strategy: `ask-on-risk` resolved by AGENTS.md → stacked-to-main chained PRs of ~400 lines.
Forecast ≈ 2950 authored lines — **already stale**: T1+T2+T3 alone measure ~3725 authored.

## Progress / evidence
- 2026-10-02: mapping done (risk empty, no ml/, issue #192 only). Owner decisions: real-source
  download authorized; harness drafted by a writer and approved by the owner; risk screen out.
- 2026-10-02: T3 landed 15 commits on lane `e10-t3` (branch `e10-t3`, worktree
  `techcamp-v2-worktrees/e10-t3`, HEAD `3f60153`). Verified in that worktree: `just gate-fast`
  green (alembic, ast-grep, ml ruff/format/mypy, eslint, tsc) and `pytest` in `ml/` 46 passed in
  0.62s with no network (fixture-backed). `gentle-ai review assess --base-ref 6988a7c
  --committed-only` → `risk: medium`, `review_due: true`, `slice_budget_reached` (2488 lines).
- 2026-10-02: Open-Meteo archive returns 429 `Daily API request limit exceeded. Please try again
  tomorrow` — a **daily** reset, not a per-minute rate limit. The writer's `until … sleep 420`
  retry loop could not succeed and was stopped; `sleep 420`/`sleep 540` orphans killed. Cache
  preserved: 3 complete chunks (`archive_00{0,1,2}.json` + `.request.json`, 12M), 0 `.part`.
  Agent `e10-t3` left idle and interactive rather than killed, to keep its 15-commit context.
  Consequence: T3 closable, **T4 blocked** on the real archive data.
- 2026-10-03: **D-T3.3 unblocked.** The free Open-Meteo archive completed on the first daily-quota
  attempt after the decision: `archive_003`, `004`, `005` cached, 6 of 6 chunks, 0 `.part`,
  `archive_plan.json` written. `parse` then produced all four source parquets with real data:
  `weather` 582,075 rows over 195 codes and 2018-06-30..2026-08-31 (`precipitation_sum`,
  `soil_moisture_0_to_7cm_mean` — the two variables docs/08 §M2 declares), `labels` 1,508 events
  over 180 municipalities and 2019–2025, `municipalities` 195, `elevation` 195.
  `labels.drops.json` records 8,477 rows dropped as `unknown_code`.
- 2026-10-05: T7 merged `e6f3060`; lane DB dropped. T5's correction in flight (no receipt yet).
- 2026-10-05: **T4's real build is unblocked and NOT run.** It writes a tracked
  `ml/datasets/flood_m2/manifest.json`, so it is implementation with its own commit, not a parent
  step. It needs the epic-branch worktree carrying `ml/.cache/raw` and `ml/data/flood_m2/sources`.

## Next step
- **T5 is the only open lane.** Its one-read guard must stop resting on object identity: key it on
  something derived from the contract, and refuse BEFORE the test block is read, not after. Then one
  more bounded review over `fefeb00..HEAD` to earn T5 a receipt. Both prior rounds on that lane are
  parked (`review-6cd12d59a63983e9` `correction_required`, `review-b34713697b28f808` escalated);
  neither burns authority and neither blocks delivery.
- **T4's real build**, now that D-T3.3 is unblocked. Needs the epic-branch worktree carrying
  `ml/.cache/raw` and `ml/data/flood_m2/sources`, then `uv run --locked --project ml python
  ml/datasets/flood_m2/build.py`. Its hash lands in the manifest and the real counts replace the
  fixture counts in the data card.
- **Dispositions the owner still owns:** the parked escalated lineages (T5 round 1 over the
  correction, T7 round 2 over `ae735b8`) and `review-c73397a8bb8c3785`'s already-acknowledged state.
  None of them blocks anything; escalation approves nothing.
- T8 experiments need T4's real build and T5's approved harness. T9 needs T8. T10 is `just
  gate-full`, the demo, and the delivery plan.
