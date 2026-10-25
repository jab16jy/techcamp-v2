# TechCamp v2 — E16 Scenario simulator (one-command A–E + expected e2e in CI)

## Objective

Deliver epic **E16** from `docs/10-dag.md:76` — *"Los escenarios A–E corren con
un comando, con fixtures de clima grabados, y su bloque `expected` pasa como
prueba end-to-end en CI (06 §10)"*, deps E4, E5, E7 — as specified in
`docs/06-diseno-detallado.md:394-490` (§10 simulator), `docs/04-api.md:263`
(`POST /dev/scenarios/{name}:load → 202`), `docs/11-metricas.md` §3
(decision-quality baseline), and ADR-0021 (seminar profile).

Feature identity: `techcamp-v2-e16-simulator`. This file is E16's only feature
doc. It does **not** replace `techcamp-v2-e16-blockers.md` (the
pre-implementation assessment, kept as the B/C evidence this plan cites) nor
any other epic's doc.

## Problem

The simulator is single-node sine only. `server/src/techcamp/simulator/`
holds `runner.py` (backfill + live loops, `publish_backfill` /
`publish_live`), `trajectory.py`, `publisher.py`, `provision.py`,
`__main__.py` with `--backfill-days` and `--live` — and **no `--scenario`**,
no multi-node, no faults, no truth export. `provision.py:4` references
`/dev/scenarios/{name}:load` as future work; no router serves
`/dev/scenarios` (contract specified at `docs/04-api.md:263`, implementation
missing — B2). There is no `firmware/` dir at all while docs/06:421 places the
library at `firmware/simulator/scenarios/<nombre>.yaml` (C1). No scenario YAML
exists (A–E + normal, docs/06:473-480), no weather fixture is recorded
(`record_weather` has zero hits in `server/src`; docs/06:489 is one bullet),
and there is no `web/e2e/` and no Playwright dependency — so the
`expected`-as-e2e rule (docs/06:485) has nothing to run on (B4).

## Why

E16 is the seminar demo's data source and the base of E15
(`docs/10-dag.md:76`, docs/06:394-396). Without it every later demo hand-builds
nodes, weather, and faults; with it one command
(`sim run --scenario el-nino --backfill 14d --live`, docs/06:408) reproduces
the exact arithmetic the docs promise — scenario A crossing θ_estrés = 15.3 %
on day 10.4 (docs/06:442-448), scenario E opening `water_stress` on day 16 and
going critical on day 18 (docs/06:465-471) — and CI guards each one through its
`expected` block. E4 (telemetry), E5 (weather), and E7 (alerts) are delivered,
so the pipeline the scenarios drive already exists.

## Scope

**In**

- Scenario library: YAMLs for `el-nino` (A), `rainy-season` (B),
  `node-failure` (C), `offline-farmer` (D), `dry-spell-rainfed` (E), `normal`
  (docs/06:473-480), at the T0-decided home (D-T16.1), with the schema of
  docs/06:421-440 extended to all six.
- Runner: `sim run --scenario <name> --backfill <dur> [--live]` — multi-node
  trajectories (docs/06:428), configurable faults (docs/06:435-436), raw ADC
  uplinks with per-node calibration (docs/06:484), deterministic seed, and
  noiseless-truth export (docs/06:487).
- `POST /dev/scenarios/{name}:load → 202` per the D-T16.2 contract (base data
  + fixtures, `docs/04-api.md:263`), seminar profile only.
- `sim record-weather` + versioned weather fixtures (`el-nino-caribe.json`,
  `veranillo-caribe.json`, docs/06:430,458) per the D-T16.3 format; CI runs
  offline (docs/06:489).
- `expected` checker + Playwright harness + CI job: every scenario's
  `expected` block passes as e2e (docs/06:485), schema per D-T16.4.
- Domain test: `water_stress` opens iff `Dr > RAW`, never before, with each
  scenario's soil/crop parameters (docs/06:486).
- Scenario D PWA offline e2e asserting E8's sync without duplicating it (C5).

**Out**

- Real browser push. Scenario A asserts the push via `GET /dev/outbox`
  (`docs/04-api.md:268`); #146 stays open (D-T16.6).
- The risk screen (#277). Scenarios assert the served risk signal as-is.
- A second simulator or a real LoRa node (E13 future work, docs/10-dag).
- Live Open-Meteo in CI. Fixtures are recorded once; CI is offline
  (docs/06:489).
- Rebuilding E8's offline sync. D asserts it; E8 owns it.

## Constraints

- **Docs are the source of truth** (AGENTS.md rule 1). T0 writes every
  decision below into its owner doc *before* the code that depends on it, in
  the same work unit. Every task cites the doc section or ADR it implements.
- **Hexagonal** (ADR-0002): `domain` has no I/O and imports nothing;
  `application` depends only on `domain`; `adapters` depend on `application`.
- **`org_id` + `404`** (AGENTS.md, `docs/09-cuellos-de-botella.md:71`,
  `docs/04-api.md:16`): scenario loading creates org-scoped base data; another
  org's resource answers `404`.
- **Determinism + seed.** Same scenario + seed replays byte-identical
  trajectories; noise and faults are configuration (docs/06:488), never
  ambient randomness.
- **Spanish is the UI language only where docs/07 requires it.**
  Identifiers, comments, docstrings, YAML keys, and this document are English;
  Spanish copy appears only in user-facing strings docs/07 governs.
- **TDD RED → GREEN → REFACTOR for every behavior change** (AGENTS.md).
  Domain tests are pure; doubles only at ports. T0 is docs-only and runs no
  pytest (C6).
- Library APIs verified with `find-docs` (ctx7) before use; no invented API
  surface. T5 verifies the Playwright API it uses.

## Decisions (T0) — `D-T16.1` … `D-T16.6`

Each one is cheap now and expensive after the library, the route, or the
harness exists. All six are written into their owner doc by T0, in the same
work unit as the doc change.

- **D-T16.1 — Scenario library home.** Resolves C1. Either docs/06:421 keeps
  `firmware/simulator/scenarios/` and T1 creates that tree (simulator code
  stays in `server/src/techcamp/simulator/`), or T0 amends docs/06:421 to the
  `server/`-adjacent path. No YAML is written before this lands — T1's files
  go to the wrong place otherwise. **Decided by owner 2026-10-07:
  `server/src/techcamp/simulator/scenarios/`** (verificado: `firmware/` no
  existe, el simulador vive en `server/` con 1 446 líneas + tests, y `firmware/`
  exigiría CI/lint nuevos); `docs/06:421` amended accordingly.
- **D-T16.2 — `/dev/scenarios/{name}:load` contract.** Resolves C2.
  `docs/04-api.md:263` names `→ 202` and nothing else. T0 specifies the
  request body (scenario name resolution, `day` anchor semantics or explicit
  no-`day`), idempotency (reload of the same scenario: replace vs refuse),
  the `202` body (`{ job_id }` vs `{ scenario, plots, nodes }`), and whether
  it enqueues backfill or only stages base data. T0 amends docs/04:263.
- **D-T16.3 — Fixture format + record-once rule.** Resolves C4. T0 fixes the
  fixture schema (daily rows keyed by scenario day, variables the balance
  needs), where version + hash live, and the rule: `sim record-weather`
  captures from Open-Meteo once, review pins the bytes, CI never calls the
  network. T0 amends docs/06:489 from one bullet to a paragraph.
- **D-T16.4 — Playwright harness + `expected` schema.** T0 fixes the
  assertion grammar of `expected` (alerts list, irrigation kind, advice list —
  docs/06:437-440,459-462 — plus timeouts and poll intervals), the harness
  shape (`web/e2e/scenarios/`), and the CI job (which scenarios run per PR vs
  nightly). AGENTS.md's "Playwright arrives with E16" closes here.
- **D-T16.5 — One-command bootstrap scope.** Resolves C3. docs/06:409 has
  `sim run` create org/farm/plot/cycle/nodes (claim) if missing; #214 says the
  seminar bootstrap cannot create an org today. T0 decides: either `sim run`
  owns org + bucket creation (and #214 is absorbed with acceptance text) or
  it prerequisites a healthy stack (and #214 stays a filed pre-step). The
  acceptance criterion names the starting state either way.
- **D-T16.6 — Outbox assertion for scenario A.** Scenario A's push is
  asserted through `GET /dev/outbox` (`docs/04-api.md:268`), not a real
  browser push; #146 stays open. T0 records this so T6's `expected` never
  waits on a service worker. No `docs/07` screen note needed.
- **D-T16.7 — Deterministic seed + truth export (recommended, found in T0).**
  The runner publishes noisy values but exports the noise-free trajectory as
  `docs/11 §3` reference with a fixed default seed and a `--seed` flag.
  T0 records it in `docs/06:487`.
- **D-T16.8 — Faults are publish pauses (recommended, found in T0).**
  An `offline` fault publishes nothing; on resume `seq` jumps and the gap is
  observed by completeness — no rewriting of the past. T0 records it in
  `docs/06:488`.

## Tasks

Checklist with stable ids. Forecasts are stated **in deliverables first**, per
the E10 calibration lesson: line forecasts under-count 2.2–6× because tests
were not in the number, and here fixtures + tests dominate. The line figure
is a floor, not a target. Delivery has **no line budget** (AGENTS.md: *never
shrink a diff to fit one*).

- [x] **T0 — Decisions into owner docs** (D-T16.1…D-T16.8): docs/06 §10
  (library home → `server/src/techcamp/simulator/scenarios/` per owner
  2026-10-07, `record-weather` paragraph, `expected`/CI paragraph, outbox +
  seed/fault rules), docs/04 (`/dev/scenarios` contract detail), this feature
  doc. No `docs/07` change. **No code, no pytest** (C6). Landed 2026-10-07 in
  the `docs/e16-blockers-assessment` worktree. **Route: parent inline** —
  planning is never delegated (E10 T0 / E12 T0 precedent).
- [x] **T1 — Scenario library + fixtures.** YAMLs A–E + normal at the T0
  home (D-T16.1), full schema per docs/06:421-440; recorded weather fixtures
  (`el-nino-caribe.json` ETc ≈ 5 mm/día, `veranillo-caribe.json` ET0 ≈ 4.2
  mm/día, no rain > 5 mm) in the D-T16.3 format with version + hash.
  Forecast: 6 YAMLs + 2 fixtures + schema validation tests, floor ~300.
  **Route: delegated** (trigger: writer; new files, frozen schema five tasks
  consume). **Depends T0.**
- [x] **T2 — Runner `--scenario` + faults + truth.** `sim run --scenario
  <name> --backfill <dur> [--live]` (docs/06:408): multi-node trajectories,
  faults (`offline` hours, out-of-range `quality = 2`, `seq` gaps —
  docs/06:488), raw ADC + per-node calibration (docs/06:484), `--seed`
  determinism, noiseless-truth export for docs/11 §3. Forecast: CLI + runner
  extension + tests, floor ~500. **Route: delegated** (trigger: writer;
  extends `simulator/`). **Depends T1.** Parallel with T3 (disjoint files:
  CLI vs `/dev` route).
- [x] **T3 — `/dev/scenarios/{name}:load` + `record-weather`.**
  Seminar-only route per D-T16.2 (base data + fixture load → 202,
  `docs/04-api.md:263`); `sim record-weather` capturing Open-Meteo once per
  D-T16.3. Org-scoped creation, `404` for another org. Forecast: route +
  loader + recorder + tests, floor ~450. **Route: delegated** (trigger:
  writer; new route + job). **Depends T1.** Parallel with T2.
- [x] **T4 — `expected` checker + domain test.** Checker asserting the
  `expected` grammar (alerts, irrigation kind, advice) against live state;
  domain test with each scenario's soil/crop parameters that `water_stress`
  opens iff `Dr > RAW` (docs/06:486) — scenario A numbers (θ_estrés 15.3 %,
  crossing day 10.4, docs/06:442-448) and E numbers (day 16 open, day 18
  critical, docs/06:465-471) as cases. Forecast: checker + domain tests,
  floor ~350. **Route: delegated** (trigger: writer; pure + checker).
  **Depends T1, T2.** Implemented 2026-10-24 on `e16-t4` (evidence below).
- [x] **T5 — Playwright harness + CI job.** `web/e2e/scenarios/` harness per
  D-T16.4 (Playwright API verified via `find-docs`), CI job running
  `expected` e2e (docs/06:485), offline (fixtures only). Forecast: harness +
  config + CI workflow, floor ~400. **Route: delegated + `impeccable`** only
  where PWA assertions need screen paths (trigger: writer, e2e). **Depends
  T4.** Implemented 2026-10-24 on `e16-t5` (evidence below).
- [ ] **T6 — Scenarios A / B / C / E / normal wired.** Each scenario runs
  one-command with its per-scenario `expected`: A (`water_stress`,
  `heat_stress`, `node_offline` → `irrigate`, outbox push per D-T16.6), B
  (`fungal_risk` → no-irrigation advice), C (node down → technician alert +
  completeness gap), E (`water_stress` critical day 18 → `rainfed` +
  `conserve_moisture`, `K = 0` path), normal (stable, contrast). Forecast: 5
  wirings + e2e assertions, floor ~400. **Route: delegated** (trigger:
  writer; scenario assembly). **Depends T2, T3, T5.**
- [ ] **T7 — Scenario D (PWA offline) e2e.** Offline logbook + sync without
  duplicates on top of E8 (C5: assert, do not rebuild); airplane-mode flow
  through the harness. Forecast: 1 e2e + fixtures, floor ~250. **Route:
  delegated** (trigger: writer; web-adjacent e2e). **Depends T5, T6.**
  Parallel with nothing (shares the harness with T6's tail — sequential).
- [ ] **T8 — Close.** `just gate-full` **once** on a clean database,
  follow-up issues for every non-blocking RDD finding, delivery plan
  (chained-to-main PRs, one per work unit, `size:exception`), epic close.
  Forecast: docs + evidence. **Route: parent.** **Depends T1–T7.**

**Lanes.** Wave 1: T0 (parent). Wave 2: T1. Wave 3: T2 ‖ T3 (disjoint: CLI
vs `/dev` route). Wave 4: T4. Wave 5: T5. Wave 6: T6. Wave 7: T7. Wave 8: T8.
Parallel ⇔ disjoint files (E10/E12 lane rationale); each lane owns its
worktree + branch + `just db-up`, one `just gate <paths…>` per unit,
`gate-full` once.

## Authorized scope

| Task | May write | Must stop and ask for |
|---|---|---|
| T0 | `docs/03`, `docs/04`, `docs/06`, `docs/07`, `docs/11`, `docs/adr/*` (new), this file | Anything outside `docs/` + `odd/tasks/`; changing an *accepted* ADR's meaning without a new one |
| T1 | Scenario YAMLs at T0 home, weather fixtures, `server/tests/simulator/**` | `server/src/techcamp/simulator/**` implementation; `ml/` |
| T2 | `server/src/techcamp/simulator/**`, `server/tests/simulator/**` | Ingestor, alerts, irrigation internals — a missing hook there is a change *there* |
| T3 | `/dev` scenarios router, `main.py` (registration only), recorder, `server/tests/**` (scoped) | `weather` module internals; production-profile routes |
| T4 | Checker, `server/tests/simulator/**` (domain) | Changing `Dr`/`RAW` semantics in irrigation — ask |
| T5 | `web/e2e/**`, `web/playwright.config.*`, `.github/workflows/` (CI job), `find-docs` verification | `web/src/design-system/**`; app source to satisfy a test |
| T6 | Scenario wiring, fixtures, `server/tests/simulator/**`, `web/e2e/**` | Another module's evaluator; E8's sync code |
| T7 | `web/e2e/**` (D flow), fixtures, `server/tests/**` (scoped asserts) | `logbook` module internals; E8 rebuild |
| T8 | This file, follow-up issues | Any source change; that is a new task with its own receipt |

No writer opens or merges a PR, and no writer edits another module's
internals to make its own task easier.

## Acceptance criteria

- [ ] `sim run --scenario el-nino --backfill 14d [--live]` runs with **one
  command** from the T0-defined starting state (docs/06:408, D-T16.5) and
  produces org + farm + plot + cycle + claimed nodes.
- [ ] Scenario A: soil moisture crosses θ_estrés = 15.3 % on day ≈ 10.4,
  `water_stress` opens ≈ 10.7 (docs/06:442-448); `expected: alerts
  [water_stress, heat_stress, node_offline], irrigation: irrigate`
  (docs/06:437-440) passes as e2e in CI.
- [ ] Scenario E: `Dr > RAW` first on day 16, critical on day 18
  (docs/06:465-471); `expected: alerts [water_stress], irrigation: rainfed,
  advice [conserve_moisture]` passes; no `prioritize_harvest` (not `late`
  stage).
- [ ] Scenarios B, C, normal: their `expected` blocks pass (B `fungal_risk`
  → no-irrigation; C node down → technician alert + completeness gap;
  normal stable).
- [ ] Scenario D: offline logbook entry syncs with no duplicates (E8 path,
  asserted not rebuilt).
- [ ] Domain test: with each scenario's soil/crop parameters, `water_stress`
  opens iff `Dr > RAW`, never before (docs/06:486).
- [ ] Uplinks are raw ADC with per-node calibration demonstrated
  (docs/06:484); same MQTT contract as a real node (docs/06:396).
- [ ] Noiseless trajectory exported per scenario as the docs/11 §3
  decision-quality baseline (docs/06:487).
- [ ] CI runs all `expected` e2e offline — recorded fixtures only, no
  network (docs/06:489).
- [ ] Another org's scenario data answers `404` (docs/04:16).
- [ ] Every task's scoped gate is green and RDD ran per work-unit commit;
  `just gate-full` ran **once** in T8.

## Checks

| When | Command | What it covers |
|---|---|---|
| Once per worktree, before any lane starts | `just db-up` | This worktree's database. Never `just db-reset` mid-epic |
| Per work-unit commit | `just gate-fast` | ruff, format, mypy, `lint-imports`, one Alembic head, ast-grep, eslint, tsc |
| Per task, only the tests it touches | `just gate server/tests/simulator` · `just gate web/e2e/scenarios` | Static checks plus **exactly** the named tests. No paths = no tests |
| Before slicing the lane into PRs | `just gate-lane origin/main` | Static checks on **every** commit of `origin/main..HEAD` |
| Once, in T8 | `just gate-full` | Clean database, then everything CI runs |

No pytest in T0 (docs-only, C6). Targeted runs: `uv run pytest
tests/<module>/test_x.py::test_name` in `server/`; `npm test -- --run
<file>` in `web/`. Server tests run in random order (`pytest-randomly`).

## Forecast and delivery

- **Delivery strategy: `ask-on-risk`** — one work unit per chained PR,
  stacked to `main`, merged in order with merge commits (never squash), each
  with its own RDD receipt. Slices carry `size:exception` (no line budget).
  Push, PR opening and merging stay with the parent/owner.
- **Forecast: 9 tasks (T0–T8), floor ~2 650 lines + 6 YAMLs + fixtures.**
  Floor, not target: E10 measured 2.2–6× under-count with tests and fixtures
  outside the number. If any task crosses its floor, the writer reports at
  the crossing moment (E10's lesson).

## Hard requirements

| # | Requirement | Source |
|---|---|---|
| H1 | One command per scenario (`sim run --scenario … --backfill … [--live]`) | docs/06:408 |
| H2 | Same MQTT contract as a real node; ingestor/alerts/irrigation cannot distinguish | docs/06:396 |
| H3 | Raw ADC uplinks, per-node calibration demonstrated | docs/06:484 |
| H4 | `expected` block of every scenario passes as e2e in CI | docs/06:485 |
| H5 | Domain test: `water_stress` opens iff `Dr > RAW` | docs/06:486 |
| H6 | Noiseless trajectory is the water-balance error baseline | docs/06:487, docs/11 §3 |
| H7 | Fixtures recorded once from Open-Meteo, versioned; demo offline except assistant | docs/06:489 |
| H8 | Scenario A arithmetic (15.3 %, crossing day 10.4, `irrigate`) and E arithmetic (rainfed `K = 0`, day 16 / critical 18) | docs/06:442-471 |
| H9 | Org isolation; another org's resource is `404` | docs/04:16, docs/09:71 |
| H10 | Deterministic seed; noise/faults are configuration | docs/06:488 |
| H11 | `/dev/*` seminar profile only, never production | ADR-0021, docs/04:256 |
| H12 | Hexagonal layers; ports only with two implementations or external-I/O doubles | ADR-0002, docs/05 |

## Conflicts to settle in the docs (docs win, AGENTS.md rule 2)

T0 resolves all six in the same work unit as the doc change. Numbering is
preserved from `techcamp-v2-e16-blockers.md` so the two documents read as one
record.

| # | Conflict | Line A | Line B | Resolution |
|---|---|---|---|---|
| C1 | Library home `firmware/…` vs real `server/src/techcamp/simulator/` | docs/06:421 | Repo layout | **D-T16.1**: T0 picks one home; docs or tree moves, not both |
| C2 | `/dev/scenarios` body/idempotency/response unspecified beyond `→ 202` | docs/04:263 | docs/04:266 (`/dev/jobs` documents `day`; scenarios do not) | **D-T16.2**: T0 writes the full contract into docs/04:263 |
| C3 | `sim run` creates org data vs #214 (bootstrap cannot) | docs/06:409 | #214 OPEN | **D-T16.5**: T0 scopes what `sim run` owns |
| C4 | Fixture provenance synthetic-vs-recorded, versioning | docs/06:489 | No fixture rule anywhere | **D-T16.3**: T0 fixes format + record-once |
| C5 | Scenario D replays E8's path | docs/06:473-480 row D | E8 on `main` | Assert, do not rebuild; T7 owns the e2e only |
| C6 | TDD per behavior change vs docs-only T0 | AGENTS.md:100 | This doc + T0 write no source | Repo rule stands for code tasks; T0 runs no pytest |

## Open questions (owner)

1. **Library home** (C1, D-T16.1): keep `firmware/simulator/scenarios/` or
   amend docs/06:421 to the `server/`-adjacent path?
2. **`/dev/scenarios` contract** (C2, D-T16.2): body, idempotency on reload,
   `202` shape — confirm or amend T0's proposal.
3. **Fixture provenance** (C4, D-T16.3): recorded-only or synthetic allowed?
   Where do version + hash live?
4. **Bootstrap scope** (C3, D-T16.5): does `sim run` own org/bucket creation
   (absorbing #214) or prerequisite a healthy stack?

## Review (RDD)

Per work-unit commit: review assess against the previous reviewed boundary,
one lineage per unit, blocking findings in a bounded correction,
non-blocking ones to one GitHub issue per review round (`review-follow-up`,
`epic:e16`, `area:*`, `type:*`) linked from here with `Refs #N`. T0's own
commit is a passive doc-only candidate (C6).

## Progress / evidence

- 2026-10-07: pre-implementation assessment landed as
  `techcamp-v2-e16-blockers.md` on `docs/e16-blockers-assessment` (base
  `706ebc6`): verdict **E16 can start**, 0 outright-blocking OPEN issues, 6
  blockers that are repo gaps or undecided docs, 6 degradations, 6 contract
  conflicts, and the T0–T8 plan this document turns into work units.
- 2026-10-07: state spot-checked directly, not from the handoff. `ls
  firmware` → missing (C1 confirmed). `provision.py:4` references
  `/dev/scenarios/{name}:load` as future work; no router serves it while
  `docs/04-api.md:263` specifies `→ 202` (B2 corrected: contract exists,
  implementation missing). `__main__.py` has `--backfill-days` + `--live`,
  no `--scenario`. `record_weather` zero hits in `server/src` (B6).
  `web/e2e/` missing, no Playwright dep (B4).
- 2026-10-07: reuse confirmed by path — `simulator/runner.py`
  (`publish_backfill`/`publish_live`, `seq = i + 1`), ingestor
  dedupe+calibration, MQTT contract + SSE, weather `upsert_daily` +
  `/dev/jobs`, FAO-56 rainfed path, six alert evaluators, E8 on `main`,
  deps E4/E5/E7 delivered.
- 2026-10-07: no ctx7 run (no external API verification needed at plan
  time); T5 verifies the Playwright API via `find-docs` when the harness
  starts.
- 2026-10-06/07: T1 implemented. U1 `80c7910` (2 fixtures + schema-library
  contract + `test_weather_fixtures.py`), U1b `eb6a1eb` (rainy-season +
  normal fixtures), U2 `6df9b36` (scenario contract `scenario_schema.py` +
  `test_scenario_schema.py`), R3-001 correction `d932658`, 6 YAMLs
  `b082076`, `test_scenario_library.py` `a636089`. Verified: server-side
  gate steps green; full simulator suite 139 passed, 27 skipped.
- T1 RDD: `6df9b36` approved (lineage `review-edd3d19eab8c0fb5`),
  non-blocking findings filed at #303 and R3-001 fixed with `Refs #303`.
  Capture transport for `b082076` and the U2 review of the same round
  returned `provider_failed` twice on the frozen binding; per the known E10
  facade/snake_case trap no foreign lineage was started and no correction
  opened — the lineage sits in `collect` and needs a maintainer runtime fix,
  not a source change. Delivery of this lane remains owner-owned.
- 2026-10-24: T2 implemented on `e16-t2` (base `6c56be1`) as four work-unit
  commits — `55dbbf1` (scenario trajectory math: ramp, daily bands, seeded
  noise via crc32 sub-seeds, per-node two-point params with ordinal 1 =
  docs/03:463-467's example, `invert_two_point` proved against the server's
  own `apply_calibration`), `3c17765` (`publish_scenario_backfill/live`:
  one publisher per node per the broker ACL of docs/04:294, `seq` counts
  ticks so an `offline` pause leaves a jump on resume — node-failure skips
  32 ticks at day 4, seq 384 → 417 —, `--seed` byte-identical replay, and
  the noiseless truth export per tick, pauses included; ~600 authored lines,
  tests dominate, sliced no further because the runner is one behavior),
  `bc4d2a0` (`provision_scenario`: org/owner/farm/plot/cycle/soil/nodes in
  one transaction with staged flushes — the unit of work cannot order
  cross-module rows without `relationship()`s), `eeb92ca` (the `run`
  subcommand beside the unchanged legacy flags, `14d`-style durations,
  `--seed` default 0, `--truth-export PATH` defaulting to
  `<scenario>-truth.json`, per-node calibration posting; node-less scenario
  E provisions and exits without MQTT; the runner never writes
  `weather_daily`, which T3 owns). Verified: touched tests 47 passed;
  simulator suite 175 passed / 27 skipped (also under `--randomly-seed=4242`);
  ruff format/check, mypy strict (260 files), lint-imports and ast-grep green.
  Fast-forward merged into `docs/e16-blockers-assessment`
  (`6c56be1` → `eeb92ca`, no conflicts — T3 had not landed) and re-verified
  in the epic worktree against its own database.
- T2 decisions: docs/06:488 read as — the out-of-range `quality = 2` reading
  belongs to *noise* configuration ("el ruido y las fallas son configurables
  … lecturas fuera de rango"), not to an `offline` pause; D-T16.8's own
  parenthetical narrows the fault to a pure publish pause, so the runner
  emits no synthetic out-of-range uplink (no scenario's noise ≤ 1.2 can push
  a value out of [0, 100] today). Truth export per D-T16.7 written separately
  from the noisy published values, byte-deterministic (sorted keys, compact
  separators, no generation timestamp), seed recorded as provenance.
  `parse_args` is now subcommand-based: T3's `record-weather` slots in as
  another `add_parser` (its lane was cut from `6c56be1` and will conflict
  textually in `__main__.py`).
- T2 RDD: pending, owner-owned — the four commits (`55dbbf1`, `3c17765`,
  `bc4d2a0`, `eeb92ca`) are native review candidates against `6c56be1`.
- 2026-10-24: **T3 implemented** on branch `e16-t3` (base `6c56be1`), worktree
  `techcamp-v2-worktrees/e16-t3`. Eight work units: `9117308` (the library
  loads the synthetic fixture — its envelope needed `synthetic_reason` as an
  optional key), `419f109` (the fixture lands in `weather_daily` through a
  `WeatherFixtureWriter` port), `159ab5e` (the seminar-only route + the
  loader), `d821130` (the recorder and `sim record-weather`), `219aa57` (the
  domain errors the load raises are translated), `d871b5f` (a refused load
  writes nothing; a broken contract answers problem+json), `f4f19ab` (the
  recorder refuses with a message, not a traceback), `0314f25` (the whole
  recording is guarded, and the pre-flight asks farms' and telemetry's write
  roles). 15 files, +2850/−8. **No push, no PR** — delivery stays owner-owned.
- T3 observations: the route is registered only under `is_seminar_profile()`
  (ADR-0021) and requires a bearer token (H9); it answers 404 for a load in
  another organization, never 403. The idempotency key is the plot's own name
  (`Escenario <scenario> <day-anchor>`), so a reload returns the same ids and
  rotates the node credential; `replace` retires the previous cycle and nodes
  because farms has no delete use case. The response adds `cycle_ids` and
  `node_credentials` to the documented `{scenario, plot_ids, node_ids}`
  (docs/04 allows adding fields): the claim's password is readable only once.
  The default anchor is today minus the fixture's observed days. The
  organization is **not** created (identity has no use case for it), so the
  loader resolves the caller's own and `sim run` (T2) has to create it first.
  One deviation from the read-only list: `scenario_library.py`'s envelope
  gained `synthetic_reason` as an optional key.
- T3 checks: `uv run pytest tests/simulator/ -q` → 188 passed, 27 skipped;
  `ruff format --check`, `ruff check`, `mypy`, `lint-imports`, `ast-grep test`
  green. RDD in three rounds: PASS-WITH-FINDINGS (0 blocking), then FAIL (two
  blocking: telemetry keeps its own `InsufficientRoleError`, which reached
  the route untranslated, and the credential-rotation test stayed green even
  without the rotation), then PASS-WITH-FINDINGS (0 blocking) twice. The
  blocking findings were corrected in the branch's own commits; the
  non-blocking ones went to #333 (a replaced rainfed parcel stays in the
  balance scope), #334 (retired nodes still open `node_offline`), #335 (the
  four fixtures share a cell and overwrite each other) and #337 (a scenario
  YAML that does not parse escapes as a traceback — the fix belongs to
  `scenario_schema.py`, T1's file). The rounds are tracked in #336.
- 2026-10-24: T3 merged into `docs/e16-blockers-assessment` (a true merge —
  both lanes diverged from `6c56be1`). Two conflicts, resolved keeping both
  reviewed designs intact: `__main__.py` keeps T2's subparsers for `run`
  beside T3's `record-weather`, which dispatches in `main()` ahead of them
  (T3's lane shipped a manual dispatch, not an `add_parser`, so both suites
  pass unchanged — the T2 prediction above reads as the hazard it was, not
  as the resolution); this doc keeps both evidence blocks. Re-verified in
  the epic worktree against its own database: simulator suite green
  (numbers in the merge's verification run), ruff, mypy strict,
  lint-imports, ast-grep green.
- 2026-10-24: **T4 implemented** on branch `e16-t4` (base `b79fc55`),
  worktree `techcamp-v2-worktrees/e16-t4`, two work units: `920e7dc` (the
  `expected` checker — a pure core judging `ScenarioExpected` against an
  observed state and naming every failed assertion, exact match both ways
  per docs/06:473-480, plus one async edge gathering state through the repos
  the stack already serves; node-scoped alerts (`node_offline`) never pass
  the plot listing's `plot_id` filter, so each of the plot's own nodes is
  asked per rule with `get_non_resolved_for_target` — the node ids the
  loader's 202 already answers), `9b1a4f8` (the domain test H5: scenario A
  crosses θ_estrés = 15,3 % on day 10,4 and the seeded 360-min rule opens
  ≈ 6 h later — not at day 10, not at day 10,5; scenario E's daily balance
  passes RAW = 77 mm on day 16 and is critical on day 18; `Dr == RAW` and
  θ == θ_estrés never open, ADR-0022's strict edge; recommendation facets:
  A `irrigate` with depth > 0, E `rainfed` + `conserve_moisture`, never
  `prioritize_harvest`). Verified: touched tests 30 passed (random and
  fixed order); simulator suite 254 passed / 27 skipped; ruff format/check,
  mypy strict (269 files), lint-imports, ast-grep green. Independent
  verification PASS on all 8 claims, 0 blocking findings; non-blocking: a
  one-off `test_provision` flake outside T4's scope (passes in isolation,
  fixed order and later runs), and the rule × node read count noted for
  T5/T6 (#338). No push, no PR — delivery stays owner-owned.
- 2026-10-24: **T5 implemented** on branch `e16-t5` (base `7c3574b`), four
  work units: `e806819` (the scenario harness — the orchestration module with
  its own parser units, `web/playwright.config.ts` carrying D-T16.4's named
  timeouts and poll intervals, the e2e tsconfig under `tsc -b`, the vitest
  split (`*.test.ts` vitest / `*.spec.ts` Playwright), `@playwright/test`
  1.64 with the API verified via `find-docs`/ctx7), `b3aff34` (the scenario E
  pilot + the two python runners the harness spawns: the checker runner
  printing T4's `ExpectedVerdict.as_dict()` — the seam its own docstring
  named — and the balance-sweep deferral, because the 04:50 task has no HTTP
  trigger), `fd7e256` (the CI workflow: per PR behind the fail-closed changes
  filter, nightly and dispatch without it, per D-T16.4's "A+E por PR y todos
  en nightly" — A arrives with T6 —, plus the `just gate web/e2e/scenarios`
  case the Checks table already promised), `43a17d5` (`ml/uv.lock`
  regenerated: the T1 revert had left it without the server's `pyyaml`,
  which broke every gate's `ml ruff check` on a current uv). Verified: `just
  gate web/e2e/scenarios` green end to end against this worktree's own stack
  (the pilot: `sim run` provisioning → dev-OTP login →
  `/dev/scenarios:load` → the 21-day irrigation replay → rainfed
  recommendation polled → the sweep through the real worker → the checker
  verdict → the PWA's rainfed decision card on a phone viewport); vitest 596
  passed (8 new), tsc, eslint, ruff, mypy, lint-imports, ast-grep green. No
  push, no PR — delivery stays owner-owned.
- T5 decisions: **D-T16.9 (found in T5)** — the harness orchestrates through
  the seams that exist: HTTP for the `/dev` routes, the CLI for
  provisioning and backfill, two runner scripts under
  `web/e2e/scenarios/support/` for what has no HTTP surface (the T4
  checker's repository composition; the balance sweep's procrastinate task,
  docs/06 §3), and the api log file for the dev OTP (docs/04:173 prints it;
  no endpoint returns it). **D-T16.10 (found in T5)** — scenario E is T5's
  pilot, the only one the merged stack runs without a broker; the CI job
  runs per PR and nightly now and T6's wirings land in both. The harness
  reads each run's OTP only from bytes appended after its own request, so
  stacked login rounds never hand out a stale code.
- **T5 finding (BLOCKED ON THE OWNER; stops T6's E wiring):** the crop
  catalog's maize stages (initial 18 / development 27 / mid 31 / late 14,
  total 90 days) do not reproduce docs/06:465-471's arithmetic, which cites
  FAO-56 Tables 12/22 (mid = 40, total 130). With `sown_days_ago: 60` the
  21-day window covers ages 61-81 (sowing day = age 0), so the catalog
  enters `late` (Kc 0.35)
  on window day 16, ETc collapses, RAW's own p-adjustment rises to ~95 mm,
  and the day-16 crossing never sustains — the recorded pipeline answers
  rainfed + `no_action` with no `water_stress`, and the checker names
  exactly the three findings the pilot pins. The same edge touches scenario
  A (ages 42-56 straddle the development/mid boundary; A's day 42 starts in
  `development`, Kc 0.75, not the mid Kc the docs' ETc ≈ 5 assumes). Owner
  decision: FAO-56 durations in the catalog (a farms-seed change, outside
  E16's authorized lanes) or the scenarios' sowing ages (a docs/06 + YAML
  change); the YAML itself flags stage durations as "pendiente de
  validación agronómica". Until it lands, the pilot asserts the checker's
  named findings instead of `passed`, so CI stays green without lying.
- T5 verification (2026-10-24, independent, default subagent — the
  `gentle-ai-verify` provider was blocked again, as in T4): **PASS-WITH-
  FINDINGS, 0 blocking**, all 8 claims PASS (scope, named timing, glue-only
  runners, the documented loop, the catalog finding DB-confirmed, the CI
  workflow, the vitest/Playwright split, and a live `just gate
  web/e2e/scenarios`). Non-blocking: the harness's timeout message named
  `/healthz` while polling `/health`, the doc's age-window prose was off by
  one, the pinned finding is a scheduled CI break until T6 flips it (the
  deliberate tripwire — sequence the owner's catalog decision with T6's E
  wiring), and the regenerated `ml/uv.lock` carries the older lockfile
  revision. The two that touch this task's own files are fixed in the
  correction commit; the other two stand as recorded.
