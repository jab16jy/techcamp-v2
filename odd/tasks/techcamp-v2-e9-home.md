# TechCamp v2 — E9 Home screen (plot status) and technician tray

## Objective
Deliver epic E9 from `docs/10-dag.md:72`: the complete home screen fed by real simulator data, and
the technician tray with the assigned farms and their open alerts. Contracts: `GET
/plots/{plot_id}/status` (docs/04 §Estado de la parcela), `GET /me/tray` (docs/04 §Visitas de
extensión y bandeja del técnico). Screens: docs/07 §Mapa de pantallas, "Inicio" items 1–5 and
"Bandeja del técnico"; web folders `features/plot-status/` and `features/visits/` (docs/07
§Estructura).

## Problem
Every data source exists (E3–E8) but nothing composes them: the home tab is a
`PlaceholderPage` (`web/src/app/routes.tsx`), neither endpoint exists, no module may read from
all six sources under the docs/05 graph, and the web query cache is not persisted in IndexedDB
(docs/07 §Datos offline requires it for "offline shows the last state with its time").

## Why
docs/07 principle "Una decisión por pantalla": the home answers "what do I do today on this plot?"
in one 3G request (docs/04). RF-19 / gap G15: the technician works from the tray. E9 is on the
critical path (E0→…→E6→E9→E15) and unblocks E15.

## Scope
- In (server):
  - `home` module (D-T0.1): read-only composition; `application` use cases call the public
    `application` facades of `farms`, `telemetry`, `weather`, `irrigation`, `alerts`, `logbook`;
    `adapters/api` router; no tables, no `domain` beyond what the payload needs.
  - Facade queries the composition needs and does not have yet (mapper, 2026-09-30):
    latest valid value per metric for a plot; health of every node of a plot; farms assigned to a
    technician across the user's orgs; open alerts for a set of plots with severity; crop stage as
    of a day exposed through `irrigation.application`; last visit per farm.
  - `GET /plots/{plot_id}/status`, `GET /me/tray`, org-isolation tests per endpoint (docs/09).
- In (web):
  - Persisted TanStack Query cache in IndexedDB (docs/07 §Datos offline; D-T0.7).
  - `features/plot-status/`: home screen items 1–5, active-plot choice (D-T0.6), decision card
    with the "why" (`PressableStatusBand`), rainfed variant (ADR-0023), open alerts (`AlertCard`),
    soil moisture with its time + 3-day forecast (`MetricTile`), sync (`SyncIndicator`) and node
    status; SSE (`useFarmEvents`) invalidates the status query.
  - Technician tray in `features/visits/`: assigned farms, critical first, last visit date, entry
    to the existing `NewVisitSheet`; for the `technician` role, Inicio opens the tray (docs/07).
  - `impeccable` skill for every screen; design frozen (E1 primitives and patterns only).
- Out:
  - `digital_adoption_index` values: E11 (D-T0.2 returns `null`).
  - Recommendation detail, sensor history, climate risk screens (docs/07 map): not in E9's row.
  - Alerts tab (still a placeholder): not in E9's row; the home lists open alerts only.
  - Playwright e2e of the home: E16 (docs/06 §10). E9 proves "real simulator data" with a server
    integration test through the ingest path plus a by-hand run on the seminar stack (T7).
  - Node battery/RSSI (`get_node_health` returns `None` today): unchanged.

## Constraints
- ADR-0002 (hexagonal; new module added to the import-linter contract), ADR-0003, ADR-0005 (PWA,
  offline), ADR-0006 + docs/07 (design system, `impeccable`, design frozen), ADR-0023 (rainfed:
  no depth/minutes, `stress` never `irrigate`), docs/05 dependency rules (facade only, no joins
  across foreign tables), docs/09 (every repository filters by `org_id`; 404 across orgs;
  isolation test per endpoint), docs/04 conventions (problem+json).
- Reuse (mapper): `farms.application.manage_plots.resolve_plot_access` (access gate, 404),
  `CropCycleRepository.get_active_for_plot`, `irrigation` `stage_for_cycle_day` (via facade),
  `query_plot_water_balance`, `query_plot_recommendation` (`RecommendationNotFoundError` → `null`),
  `query_plot_weather` (`stale` flag), `alerts` `AlertRepository.list_for_orgs`, `telemetry`
  `get_node_health` / `NodeRepository.list_for_org`, `query_plot_readings`,
  `logbook` `ExtensionVisitRepository.list_for_farm` (`visited_on`), web `apiClient` +
  `schema.d.ts` (`pnpm gen:api`), `useActiveOrgRole`, `useOrgId`, `useFarmEvents`,
  `useSyncState`, `usePendingCount`, design-system `PressableStatusBand`, `AlertCard`,
  `MetricTile`, `WaterGauge`, `StatusBadge`, `SyncIndicator`, `EmptyState`, `OfflineBanner`.
- Invariants checklist (every brief): re-read state under the lock, never trust an earlier read;
  "the day" is America/Bogota; missing evidence is a third state, never "false" or `0` (no
  reading → `null`, never a zero moisture); every behavior test carries its negative assertion;
  every cross-org read is a 404, never an empty 200 that leaks existence.

## Route and checks
- TDD: ON (AGENTS.md §Testing, owner decision 2026-09-22). Runners: server `uv run pytest` (in
  `server/`, via `just gate <paths>`), web `npm test -- --run <file>`.
- **Targeted tests only:** each task and gate runs only the tests it touches (`just gate
  server/tests/home`, `just gate web/src/features/plot-status`). `just gate-full` runs ONCE, in
  T7. Static checks (`just gate-fast`) run per task.
- Database: `just db-up` per worktree (this one: `techcamp-db-e9-home-28063041`, port 58041);
  every lane worktree runs its own `just db-up`.
- Docs are the source (AGENTS.md): T0 writes every decision into its owner doc before code;
  every brief cites the doc sections; the parent compares each slice's diff with them.
- Library APIs through `find-docs` (ctx7) only: TanStack Query persistence (T4).
- CodeGraph first in every brief (`codegraph_explore` MCP or `codegraph explore` CLI); the
  writer lists its CodeGraph calls in the report. Each lane worktree gets its own index.
- Skills passed to writers by path (`.atl/skill-registry.md`): `impeccable` (T5, T6),
  `work-unit-commits` (all), `find-docs` (T4), `fastapi` / `pydantic` (T2, T3, docs win),
  `systematic-debugging` (any failing test not caused by the task).
- Writers (owner 2026-09-28): **AGY** default (the parent gates and runs its RDD), **OpenCode**
  for mid-to-high units (runs its own RDD after the parent's gate), **Claude `odd-worker`** for
  highly complex units. One Herdr session per feature; sub-units share it; a new task number gets
  a fresh session. Planning stays with the parent (Claude Opus).
- **Lanes talk only through the parent** (owner question 2026-09-30, rejected): writers never
  message each other. Lanes share no files by construction; the only coupling is the HTTP
  contract, which T0 fixes in docs/04 before any lane starts, and `schema.d.ts`, which the web
  lane regenerates after the server lane merges. Direct chat would bypass the parent's gate and
  docs diff, spread unverified claims between agents, and blur which session owns a file.
- Evidence contract (every writer, every commit; no evidence = not done): commit hash + subject;
  authored lines prod/test (`git show --stat`); first RED line (test id + assertion); GREEN run;
  each check command with its observed result; docs/ADRs cited; CodeGraph calls. The parent
  re-runs targeted tests + `just gate-fast` on the committed sha, compares the diff with the
  owning doc sections, then RDD; both recorded here.
- Commits follow functionality (deps → shape → behavior); ~700 authored lines is a guide, not a
  cap. RDD per slice of whole commits as they land.
- Delivery: stacked-to-main chained PRs (~400 authored lines), after the owner's word.
- Dev tooling (#139, AGENTS.md §Commands): parent gate per slice = `just gate <touched tests>`
  (gate-fast: ruff, format, mypy, lint-imports, one Alembic head, ast-grep; eslint, tsc) on the
  lane's own DB; `just gate-lane feat/e9-home` on each lane before merging it and on
  `feat/e9-home` before slicing PRs; `just gate-full` once in T7. Random test order
  (`pytest-randomly`): reproduce with `--randomly-seed`. A review finding seen twice becomes an
  ast-grep rule (`rules/<id>.yml` + `rule-tests/<id>-test.yml`). CI mirrors gate-full plus
  gitleaks.

## Decisions (approved by the owner 2026-09-30; written into docs/00, 04, 05, 07 and AGENTS.md by T0)
- **D-T0.1 Where `/status` and `/me/tray` live:** a new read-only module `home` (screaming name:
  the screen it serves). It depends on the `application` facades of `farms`, `telemetry`,
  `weather`, `irrigation`, `alerts`, `logbook`; nothing depends on it. docs/05 gets the node, the
  edges and a D-note (as D14/D25). Rejected: hosting it in `alerts` (it would need a new
  `alerts → logbook` edge unrelated to alerts) or a router-only composition like `ingestor`
  (the payload has real rules — stage, null mapping, sort — that deserve application tests).
- **D-T0.2 `digital_adoption_index`:** `null` until E11; docs/04 marks the field nullable. The UI
  hides it when `null`.
- **D-T0.3 Open alerts:** `state <> 'resolved'` (open + acknowledged), the same meaning as the
  docs/03 index "Alertas abiertas de la organización"; sorted critical first, then newest.
- **D-T0.4 `latest`:** per metric, the newest valid raw reading of the plot within the last 24 h;
  a metric with none is `null` (never `0`); `at` is the time of the newest value returned, `null`
  if all are `null`. The UI shows freshness from `at` (`formatFreshness`). Same rule in T0 for
  `water_balance` (last consolidated day, `null` if none) and `recommendation` (today's, `null`).
- **D-T0.5 `NodeHealth` item:** `{ node_id, status, last_seen_at, completeness_24h }`, the
  `/nodes/{id}/health` fields that are real today (battery/RSSI stay out until a node reports
  them); docs/04 gets the shape.
- **D-T0.6 `active_cycle`:** `null` without an active cycle; `stage` is the stage key
  (`initial|development|mid|late`) or `null` when the crop has no Kc stages (`kc_source = none`);
  `day_of_cycle` is today's cycle day (America/Bogota) with `sown_on` as day 1. The web maps keys
  to Spanish labels.
- **D-T0.7 Active plot on the home:** the last plot the user opened, stored per org on the device;
  default the first plot of the first farm (API order); no plots → `EmptyState` pointing to
  Parcelas. docs/07 gets the rule.
- **D-T0.8 `GET /me/tray`:** farms where `technician_id` = the caller, across all their orgs;
  any other caller gets `[]` (no 403: an empty tray leaks nothing). Order: open critical alerts
  desc, then open alerts desc, then `last_visit_on` asc with `null` first (never visited), then
  name. `open_alerts` per farm = D-T0.3 over the farm's plots.
- **D-T0.9 Home by role:** with the `technician` role in the active org, Inicio shows the tray
  first and the plot status of any farm is one tap away; other roles see the plot status.
- **D-T0.10 Offline:** the TanStack cache is persisted in IndexedDB (docs/07) with the query key
  scoped by org; `/status` and `/me/tray` are persisted, max age 7 days; the screen shows the
  cached time with `OfflineBanner`. Storage is Dexie, as docs/07 §Flujo de datos already draws
  (`q -- persistencia de caché --> dx`); the persister package is chosen with `find-docs` in T4
  (bundle budget 200 KB, today 171 KB).
- **D-T0.11 `recommendation.rationale` in `/status`** (parent, found in T0): docs/04 called it a
  summarised `rationale[]` list with no shape. `/status` returns the stored `rationale` object
  unchanged and the web writes the "why" from it (it already carries `forecast_rain_7d_mm`, the
  7-day rain the rainfed card shows, docs/07). No second presentation format on the server.

- **D-T2.1 Soil moisture on the home** (parent, from T1 round-3 WARNING, #207): `latest.soil_moisture_pct`
  comes from the plot's representative sensors (docs/06 §5, the same rule irrigation and
  `water_stress` use: one near Zr/2 or the mean of two in the root zone); without a representative
  sensor, the newest valid reading of any depth. Keeps the home and the recommendation on the same
  evidence when it exists, and still shows a value for plots without one.

- **D-T6.1 One tap from the tray to a plot of another org** (owner, 2026-09-30): the tray spans
  all the technician's orgs, but the plot status screen works in the active org. Tapping a plot
  makes the farm's org the active one (`setOrgId`), makes the plot the active plot, and opens its
  status, with a way back to the tray. Rejected: tap only within the active org (breaks "one tap"
  for the other orgs' farms). Written into docs/07 "Bandeja del técnico".

## Open questions
- None beyond the decisions above.

## Tasks
Forecasts are authored lines (prod + tests). Route = writer and reason.

- [x] T0 Docs first (parent inline, `domain-modeling`): approved D-T0.x into docs/04 (§Estado,
  §Bandeja, NodeHealth), docs/05 (module graph + D-note), docs/07 (active plot, home by role,
  offline cache); glossary only if a new term appears. ~90. Route: parent inline (mechanical doc
  units, already understood).
- [x] T1 Facade queries (server, existing modules, each with its org filter and tests): latest
  valid reading per metric (`telemetry`), node health for a plot (`telemetry`), farms of a
  technician across orgs (`farms`), open alerts for plots (`alerts`, D-T0.3), stage as of a day
  (`irrigation.application`), last visit per farm (`logbook`). ~400 → T1a telemetry + farms,
  T1b alerts + irrigation + logbook, one session. Route: **AGY** (small queries over known repos).
  Done: lane `e9-t1` (`2b55d8e`, `3a844d6`, `7715922`, fixes `b330661` #205, `e8ef7fa` #206);
  RDD `review-45f333b518029f99`, `review-057f840b7b4b286f`, `review-3b831cc84ee3f4ae` approved;
  #207 open (owner: no more rounds).
- [x] T2 `home` module + `GET /plots/{plot_id}/status`: skeleton, import-linter contract entry,
  use case composing T1 + existing facades, null rules (D-T0.2/4/6), router, API tests (irrigated,
  rainfed, no cycle, no readings, no recommendation, stale weather) + isolation. ~550. Route:
  **OpenCode, high** (composition over six modules; the payload contract is the E9 core).
  Done: lane `e9-t2` (`747daff`, `554d090`, fix `f5d8f0a`); RDD `review-6118d3c213c1854c`
  approved, no findings.
- [x] T3 `GET /me/tray`: use case (D-T0.8 order), router, tests (multi-org technician, not
  assigned → `[]`, critical first, never-visited first) + isolation. ~300. Route: **AGY**.
  Done: lane `e9-t3` (`816e8fc`, fix `fc7b8e1` #211); RDD `review-226db8cc0b4e155b` approved;
  merged `871a1fd`; #211 open (2 SUGGESTIONs).
- [x] T4 Web offline cache: `chore(deps)` persistence packages (via `find-docs`), persister
  wiring in `queryClient`, org-scoped keys, max age, bundle check, Vitest (restore from IndexedDB
  with `fake-indexeddb`). ~200. Route: **OpenCode, high** (new dependency, cache semantics).
  Done: lane `e9-t4` (`95a27f7`, `f8a0ef3`, fixes `72b4362` #204, `dd7bf26` RDD correction,
  `3cf97a1` token digest); RDD `review-8312b6a4a9787be9`, `review-7c429333286981ab` approved;
  #209 open.
- [x] T5 Home screen (`features/plot-status/`): regenerate `schema.d.ts`, `usePlotStatus`, active
  plot (D-T0.7), decision card irrigated/rainfed with the "why", open alerts, soil moisture + 3-day
  forecast, sync + nodes, SSE invalidation, offline banner; `impeccable`, design frozen; Vitest
  per state. ~650 → T5a data + decision card, T5b alerts/weather/nodes/SSE, one session. Route:
  **OpenCode, high** (the most important screen, many states).
  Done: lane `e9-t5` (`83dff58`, `3024c7e`, `cb045d8`, fixes `d15a5db`, `6974e02`); RDD
  `review-b990eca43bc32d62` escalated, owner merged on the parent's verification; merged
  `aae1508`; #212 open.
- [ ] T6 Technician tray (`features/visits/`): `useTray`, farm rows (existing primitives), order
  from the server, last visit, `NewVisitSheet` entry, Inicio → tray for `technician` (D-T0.9),
  route test. ~350. Route: **AGY**.
- [ ] T7 Close: server integration "real simulator data" (simulator payloads through the ingest
  flush, then `/status` shows the values and the node online); by-hand run on the seminar stack
  (`just` + simulator, screenshot via `playwright-cli`); `just gate-full` (the only full run);
  acceptance ticked with evidence; doc closed. ~200. Route: **OpenCode** + parent.

Forecast total ≈ 2,740 authored lines (≈ 7–8 RDD slices, ~7 PRs).

### Lanes (parallel where no file is shared; each lane its own worktree, branch, index, DB)
- Lane S (server): T1 → T2 → T3, sequential (T2 creates `home`; T3 adds to it).
- Lane W (web): T4 starts right after T0, in parallel with T1.
- T5 starts when T2 is merged into `feat/e9-home` (needs its OpenAPI paths); T6 when T3 and T4
  are merged. T5 ‖ T3 and T6 ‖ T5 are fine (disjoint folders; `schema.d.ts` regenerated by
  whichever lands second, after merging the first).
- T7 after everything merges.

## Acceptance criteria
- [ ] `GET /plots/{plot_id}/status` returns the docs/04 payload for an irrigated plot with real
  simulator readings, and the rainfed variant never shows `irrigate`, depth or minutes.
- [ ] Missing data is `null`, never a fake zero: no cycle, no readings, no recommendation, no
  adoption index.
- [ ] `GET /me/tray` lists the technician's farms across orgs in the D-T0.8 order with open alerts
  and `last_visit_on`; any other user gets `[]`.
- [ ] Both endpoints are org-isolated (404 for another org's plot; the tray never shows a farm of
  an org the caller is not in).
- [ ] The home shows items 1–5 of docs/07 (irrigated and rainfed), updates on SSE, and opens
  offline with the last state and its time.
- [ ] A technician's Inicio opens the tray; a farm row leads to a new visit.
- [ ] All checks green; `gate-full` once at close; RDD per slice; bundle ≤ 200 KB.

## Review (RDD)
- Boundary: branch point `2fe6c78`. Docs commits are passive (structural readback).

## Progress / evidence
- 2026-09-30 Parent (Claude Opus 5.5): worktree `e9-home` on `feat/e9-home` from `main@2fe6c78`;
  DB `techcamp-db-e9-home-28063041` on 58041; CodeGraph index initialised. Docs read: docs/04
  §Estado, §Visitas y bandeja, §Nodos; docs/05 §Módulos and dependency rules; docs/07 §Datos
  offline, §Mapa de pantallas, Inicio, Bandeja; docs/10 E9 row; docs/03 alert states and indexes.
  Code map: one read-only mapper (CodeGraph), findings folded into Scope/Constraints.
- 2026-09-30 Owner approved D-T0.1–D-T0.10 ("sure, continue").
- 2026-09-30 T0 (parent inline, `domain-modeling`): docs/04 §Estado (nullable fields + rule table,
  D-T0.2–6, D-T0.11), §Bandeja (D-T0.8); docs/05 graph node `home` + 6 edges + D-T0.1 note;
  docs/07 offline cache (D-T0.10), active plot (D-T0.7), tray order/one tap (D-T0.9); glossary
  `Bandeja del técnico` (`tray`), `Alerta abierta`; AGENTS.md Layout lists `home`. No ADR (not
  hard to reverse; D-note precedent D14/D25). Anchors `05#módulos-c4-nivel-3`,
  `04#estado-de-la-parcela-pantalla-principal`, `04#visitas-de-extensión-y-bandeja-del-técnico`
  resolve. Commits `cbfc136` (docs) + `9bb6ea6` (feature doc). RDD: assess medium (AGENTS.md
  counts as executable), consent relayed, owner chose "Skip this time" → declined for this
  candidate (lineage `review-f3e5df3a235b7e4c`); plan commit `baa4d60` approved passive
  (`review-b9f85f76f8085141`).
- 2026-09-30 Lanes launched in Herdr (briefs: common + per lane, session scratchpad):
  - Lane S / T1: AGY `e9-t1` (Gemini 3.8 Flash high), worktree `e9-t1`, branch
    `feat/e9-t1-facades`, DB `techcamp-db-e9-t1-2237016153` on 61153, own CodeGraph index.
  - Lane W / T4: OpenCode `e9-t4`, worktree `e9-t4`, branch `feat/e9-t4-cache`, web only, own
    index.
- 2026-09-30 T4 (OpenCode `e9-t4`, xhigh) → `95a27f7` chore(deps) persist-client +
  async-storage-persister 5.103.2 (48 lines, lockfile; 5.104 needs a core bump, pinned to the
  resolved core) and `f8a0ef3` feat(web) persisted cache (prod 148 / tests 204). Writer evidence:
  RED `queryClient.test.ts` TypeError reading 'persistClient' ×4, `db.upgrade.test.ts` reading
  'count' (no v3); GREEN 5+2 passed; ctx7 `@tanstack/query` v5; CodeGraph ×3 (+ grep in
  node_modules). Writer also ran the full web suite (51 files / 340 passed) against the brief;
  noted. Parent gate: `just gate web/src/lib/api web/src/lib/db` 55 + 16 passed, gate-fast clean;
  `just gate-lane feat/e9-home` exit 0 on both commits; build ok; size 172.92 / 200 kB; Dexie still
  a lazy chunk. Docs diff: matches docs/07 row + D-T0.10 (Dexie v3 added, v1/v2 untouched; opt-in
  `meta.persist`; org-first keys documented; 7 d; wipe on `clearSession`, which `expireSession`
  also runs). Accepted writer calls: hand-written `CACHE_BUSTER`, `queryClient.clear()` on
  sign-out, no mutation dehydration. Parent wrote the buster/opt-in/sign-out rule into docs/07.
  RDD (OpenCode, after the parent's go): consent envelope relayed in the pane, owner chose
  "Review this change"; lineage `review-8312b6a4a9787be9`, one lens `review-reliability`,
  approved, authority burned. Target base = branch point, so it also covered the T0 docs. One
  non-blocking WARNING (R3, `session.ts:82-83`): sign-out fire-and-forgets the persisted-cache
  wipe; a reload/kill in that window leaves the previous user's cache. Touches T4's own code and
  a security invariant → issue #204 and fixed now by the same session (restore fails closed
  without a session token), `Refs #204`.
  Fix `72b4362` (+55/−2, `Refs #204`): the storage adapter's `getItem` (the persister's only
  read path) deletes the row and returns null without a session token; test removes the token
  directly to reproduce the kill window, with a token-present control. Parent gate: 56 + 16
  passed, gate-lane exit 0, size 172.92 kB. RDD on the fix candidate: OpenCode, after the go.
  RDD round 2 (OpenCode): `review-7c429333286981ab` — one CRITICAL (a same-org second user on
  the phone would get the previous user's cache: org-scoped keys do not separate users) fixed in
  the bounded correction `dd7bf26` (row bound to the writing session), validator approved. Open
  WARNING (7-day expiry gates the shared envelope, not each query) + validator notes → #209.
  Parent gate on `dd7bf26`: it stored the raw bearer token in IndexedDB → sent back (store a
  SHA-256 digest instead), same session.
  Fix `3cf97a1` (+54/−5): the row keeps a hex SHA-256 of the token, never the token (test asserts
  the whole stored value). Parent gate: 58 + 16 passed, gate-lane exit 0, size 172.92 kB. Lane
  merged into `feat/e9-home`; session closed.
- 2026-09-30 Integration RDD on `feat/e9-home` after the T1 merge (owner granted):
  `review-0249705e7774dd7e` approved, 3 findings → owner rule (3+: fix the most important):
  parent fixed both WARNINGs in `f77ee62` (alerts org-filter isolation proof; uncalibrated
  reading excluded), each with a mutation check (filter removed → test fails); SUGGESTION → #208.
- 2026-09-30 Owner rule for all later rounds: 1–2 non-blocking findings → issue only; 3+ → fix the
  most important, file the rest.
- 2026-09-30 T2 launched: OpenCode `e9-t2` (high), worktree `e9-t2`, branch `feat/e9-t2-status`
  from `86aecb1`, DB `techcamp-db-e9-t2-4156389258` on 64258.
- 2026-09-30 T1 (AGY `e9-t1`, Gemini 3.8 Flash high) → `2b55d8e` feat(telemetry,farms) T1a
  (652 lines) and `3a844d6` feat(alerts,irrigation,logbook) T1b (554 lines); tests ≈ 820 of
  1,206. Writer evidence: RED `ImportError … list_open_alerts_for_plots` (+ `crop_stage_for_day`,
  `get_latest_visit_dates`); GREEN 7 (T1b) + 6/4 (T1a); CodeGraph MCP ×11. Report cited
  endpoints that do not exist (`/api/v1/home/state`); commits cite docs correctly. Parent gate:
  `just gate` on the five `test_home_queries.py` + `test_stream_api.py` → 4+2+2+3+2+6 passed,
  gate-fast clean. Docs diff: D-T0.3 (state <> resolved, critical first, newest), D-T0.4
  (DISTINCT ON metric, 24 h, bit 2, non-null), D-T0.5 (shared `compute_node_completeness`),
  D-T0.6, D-T0.8 (technician ∧ org IN, grouped max `visited_on`, deleted excluded) match. Quality
  issues sent back (fix now, one commit): `sown_on: date | Any` + `getattr` duck-typing;
  `LatestMetricReading` duplicating `ReadingPoint`; empty-input guard duplicated in use case and
  repository. Then the parent runs RDD (AGY has none).
  Fix `7715922` refactor (+7/−33): `sown_on: date`, `ReadingPoint` reused, guards only in the
  repositories. AGY's second report still named doc sections that do not exist ("§Estado del
  lote"); corrected in the next brief. Parent gate: 19 passed, gate-lane `feat/e9-home` exit 0 on
  all three commits. RDD (parent; standing grant for feature candidates): lineage
  `review-45f333b518029f99`, lens `review-reliability`, approved, authority burned. Findings
  (non-blocking) → #205, all fixed now by AGY (AGY rule): WARNING untested InvalidCropStages
  fallback + future sowing branch; SUGGESTION tray tiebreak by id; SUGGESTION silent 500-node cap.
  Fix `b330661` (+127/−13, `Refs #205`): name+id order, paging through `list_for_org` (ascending
  id cursor, verified), stage fallback via the domain error. Parent gate: 22 passed, gate-lane
  exit 0. RDD round 2 (fix candidate, owner granted): `review-057f840b7b4b286f` approved → #206:
  WARNING future sowing gave `day_of_cycle` −4 (docs gap: parent added to docs/04 §Estado
  "siembra posterior a hoy → `day_of_cycle` y `stage` son `null`", D-T0.6 extended); SUGGESTION
  `DISTINCT ON` tiebreak by sensor id — both fixed now by AGY; SUGGESTION duplicate
  invalid-stages test left in #206 (test-only loop cap).
  Fix `e8ef7fa` (+52/−8, `Refs #206`). Parent gate: 23 passed, gate-lane exit 0. RDD round 3
  (owner granted): `review-3b831cc84ee3f4ae` approved → #207 (2 WARNING, 2 SUGGESTION). Owner
  2026-09-30: no more fix rounds, findings stay in the issue. Round-3 WARNING "soil moisture
  mixes depths" is taken by T2 (D-T2.1); "future sowing contract" is already in docs/04 on
  `feat/e9-home`. Lane merged into `feat/e9-home` (see Tasks).
- 2026-09-30 Owner: lanes that depend on T2 in anything wait for it (T3 held; its worktree
  `e9-t3` exists at `8e05144`, no agent). Cleanup (owner): T1 DB + worktree, T4 worktree, the
  merged `dev-tooling` worktree and branch removed; lane branches kept (RDD lineages).
- 2026-09-30 Integration RDD after the T4 merge (owner granted): `review-1259758590d44a8f`
  approved, 1 SUGGESTION (multi-org tray queries untested) → #210.
- 2026-09-30 T2 (OpenCode `e9-t2`, high) → `747daff` feat(home) use case (prod 514 / tests 853)
  and `554d090` feat(home) router (prod 291 / tests 308); 1,967 lines vs ~550 forecast (fixtures
  across nine tables). RED `ModuleNotFoundError techcamp.home`, then `assert 404 == 401`; GREEN
  13 → 22 passed; CodeGraph MCP ×5; other modules' suites run (no regressions). Parent gate: 22
  passed, gate-fast clean, gate-lane exit 0, imports facade-only. Rulings: `plot` as a compact
  `PlotSummary` (no polygon) ACCEPTED, written into docs/04 §Estado; `water_balance` = last of
  30 days REJECTED (docs/04: yesterday's or null) → fixed by the same session, then its RDD.
  Fix `f5d8f0a` (+44/−2): yesterday's row or null, RED "3-day-old balance → null". Parent gate:
  23 passed, gate-lane exit 0. RDD (OpenCode; consent relayed in the pane, owner chose "Review"):
  `review-6118d3c213c1854c` approved, zero findings (target covered T0+T1+T2 from the branch
  point). Writer note, not a finding: `/status` resolves plot access four times (the reused
  facade queries each check it) — SQL round trips, not HTTP; revisit in T7 if latency shows it.
  Lane merged into `feat/e9-home`; session closed.
- 2026-09-30 T2 merged (`4894315`), post-merge `just gate server/tests/home` 23 passed; T2 DB and
  worktree removed. T3 and T5 launched in parallel (disjoint: server `home` tray vs web
  `features/plot-status`):
  - T3: AGY `e9-t3` (Gemini 3.8 Flash high), worktree `e9-t3` recreated from `4894315`, DB
    `techcamp-db-e9-t3-631551574`; must prove #210's multi-org path end to end.
  - T5: OpenCode `e9-t5` (high), worktree `e9-t5`, web only; `impeccable`, design frozen; T5a
    (schema regen + decision card + active plot) then T5b on the parent's go.

- 2026-09-30 Parent session 2 (Claude Opus 5.5) resumed from the handoff. The AGY `e9-t3` pane
  was closed by accident mid-task; the same conversation was resumed in a new pane
  (`agy --conversation <id>`) and continued from its last step.
- 2026-09-30 T5a blocked: T2's `home` router reused `PlotView`, `CropView`, `NodeHealthView`
  (farms/telemetry own them), so FastAPI emitted module-qualified OpenAPI names and the web lost
  the plain types. Parent fix on `feat/e9-home`, `f7bd9a9` (+35): views renamed
  `PlotSummaryView`, `CycleCropView`, `PlotNodeHealthView`; new test
  `server/tests/test_openapi_schema_names.py`. RED `assert ['techcamp__f...deHealthView'] == []`,
  GREEN 1 + 23 (`just gate server/tests/test_openapi_schema_names.py server/tests/home`). RDD on
  the whole branch failed with `lens_context_budget_exceeded` (`review-a872e9e965ddb8df`, no
  authority created); scoped to `f7bd9a9`, owner chose "Skip this time"
  (`review-d0364dcf9dfc1520` declined). The T5 writer was told not to touch the server.
- 2026-09-30 T3 (AGY `e9-t3`) → `816e8fc` feat(home) `GET /me/tray` (prod 252 / tests 536).
  Writer evidence: RED/GREEN `just gate server/tests/home` 31 passed; CodeGraph ×9; multi-org
  path proven end to end (#210). Parent gate: diff read against docs/04 §Visitas y bandeja and
  docs/05 D-T0.1; `just gate server/tests/home` 31 passed, gate-fast clean, gate-lane exit 0.
  Ruling: `farm` as a compact `FarmSummary` (no geometry, no technician) ACCEPTED, written into
  docs/04 §Bandeja. RDD (parent, standing grant, scoped `--base-ref 4894315 --committed-only`):
  `review-226db8cc0b4e155b`, lens `review-reliability`, approved, authority burned. 3
  non-blocking findings → rule 3+ → #211: WARNING (per-farm alert order unproved) fixed now,
  SUGGESTIONs (plot N+1 per farm, tiebreak coverage) filed.
  Fix `fc7b8e1` (+74, `Refs #211`): explicit per-farm sort (critical, newest, id); new test with
  one farm, two plots, three alerts, exact id order plus a negative assertion. The test passed
  before the fix (the upstream query already orders), so it pins the behavior, not the sort line.
  Parent read the diff; gate 32 passed, gate-lane exit 0. Merged `871a1fd`; post-merge
  `just gate server/tests/home server/tests/test_openapi_schema_names.py` 32 + 1 passed.
  Integration candidate on `feat/e9-home` relayed, owner chose "Skip this time"
  (`review-1eb74c6fc408543e`). T3 DB, worktree and pane removed; branch kept.
- 2026-09-30 T5a (OpenCode `e9-t5`) → `83dff58` chore(web) schema regen (222 generated) and
  `3024c7e` feat(web) decision card (prod 723 / tests 951). RED 4 files unresolved imports;
  GREEN 54 then 178. Parent gate: diff read against docs/07 Inicio items 1–2, ADR-0023, D-T0.7,
  D-T0.10; E1 primitives only (`PressableStatusBand`, `EmptyState`, `Button`); `just gate` on
  plot-status, lib/api, plots, routes, App → 46+66+57+7+2 passed; gate-lane exit 0; size
  173.88/200 kB. Rulings ACCEPTED (written into docs/07 item 2): rainfed band from today's
  rationale (stress above RAW, watch at RAW); the "why" is evidence, not the decision again;
  unknown `kind` or `irrigate` without depth → "Aún no hay recomendación para hoy". Quality issue
  sent into T5b: offline cold start (the home waited for farm/plot lists that are not persisted).
  `impeccable`: loaded at session start (OpenCode log), but T5a's report showed no checks → the
  writer ran them for T5b (craft-floor, operate, harden, clarify; see its report).
- 2026-09-30 T5b → `cb045d8` feat(web) items 3–5, SSE, OfflineBanner, offline fix (prod 569 /
  tests 643). RED `Failed to resolve import "./statusCopy"`; offline RED "resolves the remembered
  plot without waiting for the lists" showed only "Cargando parcelas…". The writer's RDD first
  froze the whole branch (74 paths); parent ruling: selectors go on STATUS
  (`--base-ref f7bd9a9 --committed-only`), the returned START runs unchanged → 22 web files.
  RDD `review-b990eca43bc32d62` (owner granted in the pane): R3 CRITICAL (one farm's failed plot
  list blanked the whole home) → bounded correction `d15a5db` (prod +28/−14, tests +35); the validator accepted the fix
  and rejected the correction for a regression (a later farm's plot shown while the first still
  loaded) → state escalated. The writer fixed the regression in `6974e02` (prod +12/−1, tests +51) outside the
  correction. Parent read the final `useActivePlot` (failed farm = final, use the rest; pending
  farm = no default yet; remembered plot answers at once; 404 forgets it); gate 81+66+57+7+2
  passed, gate-lane exit 0 on all 5 commits, size 174.09 kB. Owner: merge on the parent's
  verification, no further round. Merged `aae1508`; post-merge gate same 213 passed. Findings →
  #212 (the default is remembered after a transient partial failure; `formatPercent` uses `.`
  while `es-CO` uses `,`). T5 pane and worktree removed (web only, no DB); branch kept.
- 2026-09-30 Docs (parent): docs/04 §Bandeja `farm` shape; docs/07 Inicio item 1 (remembered plot
  without the lists, 404 forgets it, partial farm failure) and item 2 (rainfed band, the why,
  unknown kind).

## Next step
T6 (tray screen, AGY; needs T3 + T4, both merged): brief `e9-t6.md` (docs/07 "Bandeja del
técnico", D-T0.9 technician Inicio → tray, `NewVisitSheet` entry, persisted `[orgId, 'tray']`,
bump `CACHE_BUSTER`, regenerate `schema.d.ts` for `/me/tray`). Then T7 (close: simulator
integration, by-hand seminar run, gate-full once).
