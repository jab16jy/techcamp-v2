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

## Open questions
- None beyond the decisions above.

## Tasks
Forecasts are authored lines (prod + tests). Route = writer and reason.

- [x] T0 Docs first (parent inline, `domain-modeling`): approved D-T0.x into docs/04 (§Estado,
  §Bandeja, NodeHealth), docs/05 (module graph + D-note), docs/07 (active plot, home by role,
  offline cache); glossary only if a new term appears. ~90. Route: parent inline (mechanical doc
  units, already understood).
- [ ] T1 Facade queries (server, existing modules, each with its org filter and tests): latest
  valid reading per metric (`telemetry`), node health for a plot (`telemetry`), farms of a
  technician across orgs (`farms`), open alerts for plots (`alerts`, D-T0.3), stage as of a day
  (`irrigation.application`), last visit per farm (`logbook`). ~400 → T1a telemetry + farms,
  T1b alerts + irrigation + logbook, one session. Route: **AGY** (small queries over known repos).
- [ ] T2 `home` module + `GET /plots/{plot_id}/status`: skeleton, import-linter contract entry,
  use case composing T1 + existing facades, null rules (D-T0.2/4/6), router, API tests (irrigated,
  rainfed, no cycle, no readings, no recommendation, stale weather) + isolation. ~550. Route:
  **OpenCode, high** (composition over six modules; the payload contract is the E9 core).
- [ ] T3 `GET /me/tray`: use case (D-T0.8 order), router, tests (multi-org technician, not
  assigned → `[]`, critical first, never-visited first) + isolation. ~300. Route: **AGY**.
- [ ] T4 Web offline cache: `chore(deps)` persistence packages (via `find-docs`), persister
  wiring in `queryClient`, org-scoped keys, max age, bundle check, Vitest (restore from IndexedDB
  with `fake-indexeddb`). ~200. Route: **OpenCode, high** (new dependency, cache semantics).
- [ ] T5 Home screen (`features/plot-status/`): regenerate `schema.d.ts`, `usePlotStatus`, active
  plot (D-T0.7), decision card irrigated/rainfed with the "why", open alerts, soil moisture + 3-day
  forecast, sync + nodes, SSE invalidation, offline banner; `impeccable`, design frozen; Vitest
  per state. ~650 → T5a data + decision card, T5b alerts/weather/nodes/SSE, one session. Route:
  **OpenCode, high** (the most important screen, many states).
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
  resolve. Review: passive documentation → structural readback.

## Next step
Launch Lane S (T1, AGY, worktree `e9-t1`) and Lane W (T4, OpenCode, worktree `e9-t4`) in Herdr.
