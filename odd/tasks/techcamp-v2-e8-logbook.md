# TechCamp v2 — E8 Offline logbook and extension visits

## Objective
Deliver epic E8 from `docs/10-dag.md:71`: scenario D (a harvest recorded in airplane mode) syncs
without duplicates, and an extension visit recorded without signal syncs too
(docs/03 §`extension_visit`). Module `logbook` (docs/05 §Módulos: `logbook → farms`), web
`lib/db`, `lib/sync`, `features/logbook`, `features/visits` (docs/07 §Estructura).

## Why
RF-10 (offline field logbook with photos, linkable to an alert), RF-19 (extension visits offline,
org export) (docs/01:33,38); RNF-01 (nothing recorded offline is lost) and RNF-06-style
idempotency. E8 unblocks E11 (yield, costs, margin, `record_keeping`, `risk_management`), E6's
irrigation entries feeding the water balance (ADR-0009), and E9 (tray `last_visit_on`).

## Scope
- In (server):
  - Tables (docs/03:216-268, index docs/03:518): `logbook_entry` (typed columns + one `CHECK` per
    `kind`, `sold_kg ≤ yield_kg`), `extension_visit` (`topics` closed vocabulary, docs/03:430-438),
    `attachment` (`CHECK` exactly one of `logbook_entry_id` / `extension_visit_id`), one global
    sequence for `server_version`, index (`org_id`, `server_version`) on both synced tables.
  - Sync protocol (docs/04 §Bitácora, docs/06 §7, ADR-0013): `POST /sync/push` (per change
    `applied|duplicate|conflict_overwritten|rejected`, LWW by `client_updated_at`, clock > 24 h
    ahead rejected, `alert_id` of the same plot), `GET /sync/pull?since=&limit=500`.
  - Visits read API (docs/04 §Visitas): `GET /farms/{farm_id}/visits`,
    `GET /organizations/{org_id}/visits?from=&to=`.
  - Photos (ADR-0018, docs/04:158): `POST /attachments:presign` against MinIO (seminar,
    ADR-0021).
- In (web): Dexie local store + outbox, the synchronizer (triggers from docs/06 §7), logbook
  screen with new-entry sheet per `kind`, new-visit sheet, sync status, client photo compression
  (≤ 200 KB, EXIF stripped). Design frozen: E1 primitives and patterns only (`FormSheet`,
  `ListWithFilters`, `OfflineBanner`, `SyncIndicator`, `EmptyState`).
- Out:
  - `GET /me/tray` and the technician tray screen: E9 (docs/10:72).
  - Metrics over the logbook (docs/11): E11. Sync success ratio metric (docs/11:90): E11/E14.
  - Playwright airplane-mode run of scenario D: E16 (docs/06:466). E8 proves D with server
    integration tests plus a Vitest offline→online sync test.
  - Offline base maps (RF-18, deferred). Voice notes (docs/01:73).
  - Orphan-object cleanup job (ADR-0018 consequence): follow-up issue unless T9 lands under budget.

## Constraints
- ADR-0002 (hexagonal; `logbook` already in the import-linter contract, pyproject:90), ADR-0003,
  ADR-0005 (PWA, Dexie, `navigator.storage.persist()`), ADR-0013 (sync), ADR-0018 (presigned
  upload, API never receives bytes), ADR-0021 (MinIO in seminar), ADR-0006 + docs/07 (design
  system; `impeccable` skill; design frozen), docs/09 (every repository filters by `org_id`;
  404 across orgs; isolation test per endpoint), docs/04 conventions (problem+json, cursor pages).
- Reuse: `shared/ids.uuid7` (server), `farms` repositories (`SqlAlchemyPlotRepository.get`,
  `SqlAlchemyFarmRepository.get`), `identity` `MembershipRepository` + `Role`, `farms.domain`
  `ensure_can_write`, alerts `Alert.plot_id`, web `lib/api/client.ts` + `session.ts`, E1 patterns.
- Invariants checklist (every brief): re-read state under the lock, never trust an earlier read;
  "the day" is America/Bogota; missing evidence is a third state, never "false"; every behavior
  test carries its negative assertion; nothing written offline is dropped silently (a `rejected`
  change stays local with its error).

## Route and checks
- TDD: ON (AGENTS.md §Testing, owner decision 2026-09-22). Runners: server `uv run pytest`,
  web `npm test -- --run`.
- **Targeted tests only (owner 2026-09-28, time budget):** every task and gate runs only the
  tests it touches (`uv run pytest tests/logbook`, `npm test -- --run src/lib/sync`, …). The
  full server and web suites run ONCE, at the end of E8 (T10). Static checks are cheap and run
  per task.
- Server checks (in `server/`, `DATABASE_URL=postgresql+asyncpg://techcamp:techcamp@localhost:5441/techcamp`,
  container `techcamp-e8-db`): targeted `uv run pytest tests/logbook[/...]`, `uv run ruff check`,
  `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`.
- Web checks (in `web/`): `npm run lint`, `npm run typecheck`, targeted `npm test -- --run <paths>`,
  `npm run build`, `npm run size` (≤ 200 KB gzip initial JS, RNF-02).
- Size (owner, E7): **≤ 700 authored lines per commit** (hard ceiling; split into work-unit
  commits deps → shape → behavior above it); **~400 authored lines per RDD slice**, reviewed as it
  lands, never one review at the end.
- Writers (owner 2026-09-28): **AGY** default (fast; no RDD of its own: the parent gates and runs
  RDD on its commits), **OpenCode** for mid-to-high units (runs its own RDD after the parent's
  gate), **Claude Opus 5.5 / `odd-worker`** for highly complex units. One writer session per
  feature (sub-units share it). Planning stays with the parent (Claude Opus).
- Evidence contract (every writer, every commit; no evidence = not done): commit hash + subject;
  authored lines split prod/test (`git show --stat`); first RED line (test id + assertion);
  GREEN run; each check command with its observed result; docs/ADRs cited; CodeGraph fallback if
  any. The parent re-runs targeted tests + lint/types on the committed sha and compares the diff
  with the owning doc sections before RDD, and records both here.
- Delivery: stacked-to-main chained PRs (~400 authored lines). E8 branches from `feat/e7-alerts`
  at `3120dac` (E7 is not on `main` yet: its migrations and the AGENTS.md rule change are needed);
  rebase onto `main` once E7 merges.

## Decisions (approved by the owner 2026-09-28; written into docs/03, 04, 06 §7 by T0)
- **D1 Commit-ordered `server_version`.** A plain sequence is not commit-ordered: a transaction
  that took 10 can commit after one that took 11, and a client that already pulled `since=11`
  never sees 10. Push takes `pg_advisory_xact_lock(<sync key>)` before `nextval` so allocation
  order equals commit order. `# ponytail:` global lock; move to a snapshot-xmin watermark if push
  throughput ever matters. Owner doc: docs/06 §7.
- **D2 Pull scope.** `GET /sync/pull` returns `logbook_entry` and `extension_visit` rows of every
  org the caller is a member of, merged by `server_version` ascending; `deleted_at` rows come back
  as `op: "delete"`. One cursor covers both tables (same sequence). Owner doc: docs/04 §Bitácora.
- **D3 Write roles.** `logbook_entry`: `owner`, `technician`, `producer`; `viewer` → `rejected`.
  `extension_visit`: `technician` only, and `technician_id` must equal the caller (docs/03:442
  invariant). Owner doc: docs/04 §Bitácora.
- **D4 Push decision (domain, pure).** Given the row read under `SELECT … FOR UPDATE` (D1 lock
  held) and the incoming change: no row → `applied`; same `client_updated_at` → `duplicate`, no
  write (docs/06:312); incoming newer → `applied`; incoming older → `conflict_overwritten`, no
  write, response carries the stored `server_version`. An `id` owned by another org, or of the
  other entity, → `rejected` with the same error as a missing plot (no existence leak, docs/09).
- **D5 `rejected` reasons** (stable `error` codes the UI maps to copy): `clock_skew`
  (`client_updated_at` > now + 24 h), `invalid` (kind CHECK / field rules), `not_found` (plot,
  farm, cycle, alert or id not visible to the caller), `alert_plot_mismatch`, `forbidden` (role).
  Each change runs in its own savepoint; one rejection never sinks the batch. Batch ≤ 100
  changes, more → `422`. Owner doc: docs/04 §Bitácora.
- **D6 `crop_cycle_id`** is nullable; when present it must be a cycle of the same plot
  (`not_found` otherwise). The client sends the plot's active cycle from its cache. Owner doc:
  docs/03 §`logbook_entry`.
- **D7 Client apply rule.** Pull never overwrites a local row that has a pending outbox change;
  the push decides it (LWW). `conflict_overwritten` and `rejected` are shown on the entry, and a
  rejected change stays local until the user fixes or discards it. Owner doc: docs/06 §7.
- **D8 Photos.** Presign creates the `attachment` row (`object_key`, `content_type`, `bytes`);
  `bytes` ≤ 200 KB and `content_type` in `image/jpeg|image/webp`, else `422`. The parent entity
  must already be synced (visible to the caller), else `404`. There is no confirm endpoint: the
  row exists from presign, and an upload that never lands is an orphan for the cleanup job
  (ADR-0018). The flow in docs/06:303 ("confirmar") is corrected to match. Owner docs: docs/04
  §Bitácora, docs/06 §7.
- **D9 Visits read API.** `GET /farms/{farm_id}/visits`: any member of the farm's org.
  `GET /organizations/{org_id}/visits`: `owner` and `technician` (export, RF-19); others `403`,
  non-members `404`. Both are cursor pages ordered newest first. Owner doc: docs/04 §Visitas.

## Open questions
- None.

## Tasks
Forecasts are authored lines (prod + tests). Route = writer and reason.

- [x] T0 Docs first: write D1–D9 into docs/03, docs/04, docs/06 §7 (`domain-modeling` skill);
  no ADR needed (ADR-0013 stands; D1 is its implementation detail). ~80. Route: parent inline
  (one mechanical doc unit per decision, already understood).
- [ ] T1 Schema: migration from the E7 head for the sync sequence, `logbook_entry`,
  `extension_visit`, `attachment`, CHECKs and indexes; ORM rows. Test: migration up/down + CHECK
  violations. ~300. Route: **AGY** (mechanical, docs/03 is exact).
- [ ] T2 Logbook domain (pure): entry fields per `kind`, visit invariants (topics vocabulary),
  push decision D4, clock-skew rule D5. ~350. Route: **AGY**.
- [ ] T3 Sync push: ports, repositories (lock D1, `FOR UPDATE`, savepoint per change), use case
  with role/plot/cycle/alert/farm checks, `POST /sync/push`, isolation + concurrency tests (two
  pushes of the same id). ~650 → T3a repositories + use case, T3b router + isolation/concurrency
  tests, one session. Route: **OpenCode, xhigh** (concurrency and ordering).
- [ ] T4 Sync pull: merged cursor across both tables, `has_more`, deletes, org scope (D2);
  test that a late-committing push is never skipped (D1). ~300. Route: **AGY**.
- [ ] T5 Visits read API (D9) + isolation tests. ~250. Route: **AGY**.
- [ ] T6 Presign (D8): S3 client for MinIO (library via `find-docs`; `chore(deps)` commit first),
  config, `POST /attachments:presign`, tests. ~350. Route: **OpenCode, high** (new external
  dependency and config).
- [ ] T7 Web local store + synchronizer: Dexie schema (`lib/db`), outbox, push batches ≤ 100,
  pull cursor, triggers (open, `online`, 60 s visible, 2 s after write), token refresh first,
  `storage.persist()`, D7 apply rule; Vitest with `fake-indexeddb`; bundle budget checked.
  ~650 → T7a store + outbox, T7b synchronizer, one session. Route: **OpenCode, high**; escalate
  to `odd-worker` if T7b's state handling stalls.
- [ ] T8 Logbook screen (`features/logbook`): list from Dexie, new-entry `FormSheet` per `kind`
  (harvest with `sold_kg` / price, task with `labor_days`, link to a cached alert), "Guardado en el
  teléfono" state, conflict/rejected notices, `SyncIndicator` wired. `impeccable`, design frozen.
  ~550 → T8a list + sync state, T8b entry sheet, one session. Route: **AGY**.
- [ ] T9 Visits screen (`features/visits`): new visit sheet from a farm (five Ley 1876 topics),
  offline, plus client photo compression and pending upload queue for entries and visits.
  ~450 → T9a visit sheet, T9b photos, one session. Route: **AGY** (T9a), **OpenCode** (T9b, canvas
  re-encode + upload queue).
- [ ] T10 Close: scenario-D server integration (same batch twice → one row, `duplicate`; stale
  edit → `conflict_overwritten`), Vitest offline→online harvest with no duplicate, visit offline
  sync; the FULL server and web suites (the only full run in E8); acceptance ticked with evidence;
  feature doc closed. ~250. Route: **AGY** + parent.

Forecast total ≈ 4,130 authored lines (≈ 10–11 RDD slices, ~9 PRs).

## Acceptance criteria
- [ ] Pushing the same change twice yields one row and `duplicate` the second time.
- [ ] Two devices editing one entry: the newer `client_updated_at` wins; the older push gets
  `conflict_overwritten` and the UI shows it.
- [ ] A push committed after a later-sequenced one is still returned by pull (D1).
- [ ] A harvest recorded offline in the web client is stored locally, syncs when online, and
  appears once on the server.
- [ ] An extension visit recorded offline syncs the same way; only technicians can push visits.
- [ ] Every sync, visit and attachment path is org-isolated (`rejected`/404 across orgs).
- [ ] All server and web checks green; RDD per ~400-line slice; no commit over 700 authored lines.

## Review (RDD)
- Boundary: branch point `3120dac`.

## Progress / evidence
- 2026-09-28 Parent (Claude Opus 5.5): worktree `e8-logbook` on `feat/e8-logbook` from
  `feat/e7-alerts@3120dac`; test DB `techcamp-e8-db` on 5441; CodeGraph index initialised. Docs
  read: docs/00, 01 (RF-10, RF-19, RNF-01), 03 §logbook_entry/§extension_visit, 04 §Bitácora
  and §Visitas, 06 §7, 07 (structure, offline data flow), 10 (E8 row), ADR-0005, 0013, 0018.

- 2026-09-28 T0 (parent inline, `domain-modeling`): owner approved D1–D9; written into docs/03
  §logbook_entry (D6), docs/04 §Bitácora (D2, D3, D4, D5, D8) and §Visitas (D9), docs/06 §7 (D1,
  D7, D8 flow without "confirmar"). No glossary term or ADR needed (D1 implements ADR-0013).
  Evidence: `git diff --stat` 3 docs, +32/−2; anchors `09#seguridad` and `04#bitácora-…` resolve.
  Review: passive documentation only → structural readback, no RDD.
- 2026-09-28 Owner rule: targeted tests only per task and gate; full suites once at T10.

## Next step
T1 (schema) on AGY.
