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
- Size (owner 2026-09-28, corrected): **commits follow functionality**, one coherent behavior with
  its tests per commit (deps → shape → behavior for large units). ~700 authored lines is an
  estimate, not a cap: 300–400 or ~770 are all fine. **An RDD slice covers whole functional
  commits** (one or a few), reviewed as they land, never cut at a fixed 400-line count and never
  one review at the end.
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
- **D10 Per-`kind` CHECKs** (parent, 2026-09-28, closing the gap docs/03 left as one example):
  required `harvest ⇒ yield_kg`, `irrigation ⇒ irrigation_mm`, `task ⇒ labor_days`,
  `input`/`cost ⇒ cost_cop`; exclusive `yield_kg`/`sold_kg`/`sale_price_cop_per_kg` harvest-only,
  `labor_days` task-only, `irrigation_mm` irrigation-only; `sold_kg` and price together,
  `sold_kg ≤ yield_kg`; non-negative amounts. Derived from docs/03's metric-fields table. Owner doc:
  docs/03 §`logbook_entry`.
- **D11 Expired token, seminar** (parent, 2026-09-28): there is no refresh endpoint (docs/04
  §dev auth has only OTP), and `lib/api/client.ts` signs out on `401`. The synchronizer stops on
  `401` without touching the outbox; after the user signs in again it pushes what is pending;
  signing out never clears Dexie. `ponytail:` the outbox is per device, so on a shared phone the
  next user pushes it (the server stamps `created_by` = caller); per-user outbox if shared phones
  appear. Owner doc: docs/06 §7.
- **D12 Delete payload** (parent, 2026-09-28, raised by T7): `op: "delete"` carries the row as
  last saved, without `deleted_at`; the server sets the tombstone and never validates fields (a
  change rejected as `invalid` can still be deleted). Stored row → D4 decides; if it wins, set
  `deleted_at`, `client_updated_at`, new `server_version`. Never stored (created and deleted
  offline) → `applied`, nothing written, `server_version: null`. Owner doc: docs/04 §Bitácora.
- **D13 Deletes are final** (parent, 2026-09-28, raised by T7): docs/07 has no undelete; the client
  keeps the local `deleted_at` on a re-save, and the server answers an `upsert` on a deleted id
  with `rejected` (`not_found`). Owner doc: docs/04 §Bitácora.

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
  teléfono" state, conflict/rejected notices, `SyncIndicator` wired. First commit: regenerate
  `schema.d.ts` (T3/T4 paths) and swap T7's hand-typed `lib/sync` transport to `apiClient`.
  The shell calls `requestPersistentStorage()` once on start (ADR-0005) and starts the
  synchronizer. Test notes from T7: fake-indexeddb needs `setImmediate` unfaked (explicit
  `toFake` list); a union of two Dexie tables types `put()` as the intersection (dispatch per entity). `impeccable`, design frozen.
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
- [ ] All server and web checks green; commits by functionality; RDD per slice of whole commits.

## Review (RDD)
- Boundary: branch point `3120dac`; docs commits through `9d31e90` are passive (structural readback).
- T1 `f242508`: parent-run RDD, assess medium (`executable_change` migrations/env.py), consent
  granted (standing new-feature grant), lineage `review-b3312c6f95cbe5ea`, one lens
  `review-reliability` → **approved**, acknowledged (`gentle-ai.review-acknowledged/v1`).
  Non-blocking: R3-orm-sequence-not-in-metadata (WARNING), R3-harvest-exclusive-untested
  (WARNING), R3-assert-fails-unspecific (SUGGESTION) → issue
  [#142](https://github.com/jab16jy/techcamp-v2/issues/142). The two WARNINGs affect T1's code:
  fixed now by `e8-t1` in one commit `Refs #142`; the SUGGESTION stays in the issue.
- T2 `f1cbbaf..9519fb1` (in `e8-t2`): parent-run RDD, assess medium, 805 lines, consent granted,
  lineage `review-179da1ed2198145c`, lens `review-reliability` → **approved**, acknowledged.
  Non-blocking → issue [#143](https://github.com/jab16jy/techcamp-v2/issues/143):
  R3-ensure-can-sync-fail-open (WARNING) — caused by the parent's gate: I had the writer drop the
  `else` as an "unreachable fallback", which made an authorization guard fail open; lesson:
  a guard denies by default, so fix it with a per-entity role mapping (KeyError on unknown), not a
  dead branch. R3-decimal-nan-escapes-domain-error (WARNING): NaN/Infinity amounts. Both fixed now
  by `e8-t2`, one commit `Refs #143`. R3-weak-exception-assertions (SUGGESTION) stays in the issue.
- T1 fix `08dd5ef` (`Refs #142`, +18/−2): gate 19 passed, static clean; assess `under_budget`
  (20 lines) → pending, reviewed inside T5's first slice (base `f242508`).
- T2 fix `174a7ac` (`Refs #143`, 74 lines): gate 44 passed, static clean; assess `under_budget`
  but reviewed now (authorization guard, and the T2 lane carries no later commits); consent is a
  fix candidate → relayed, **owner granted**; lineage `review-8d2d19fb3861286a` → **approved**,
  acknowledged. Round 2 on #143: R3-unmapped-entity-keyerror (WARNING, bare `KeyError` → 500)
  fixed with `.get(entity, frozenset())`, its commit reviewed inside T3's first slice (no fix-review
  loop); R3-removed-public-role-constants verified harmless (0 importers); sNaN coverage
  SUGGESTION stays in the issue.
- T7a `182420b` (OpenCode, 640 lines: prod 395 / test 245): writer RED ×3 (incl. two real bugs:
  local fields leaking into the payload, Dexie connection cached after a failed transaction), GREEN
  7 tests. Parent gate: lint, typecheck clean; `npm test -- --run src/lib/db` 3 files / 7 passed;
  build ok; size 163.38 kB. No `structuredClone` polyfill needed (jsdom provides it). Go given for
  OpenCode's own RDD over `fe7fb09..182420b` (base `9d31e90`). Its two open points became D12, D13.
- T7a RDD (OpenCode-run, owner-grant relayed by the parent in the pane): lineage
  `review-fb0c138492a9057a` → **approved**, acknowledged. R3-persistent-storage-rejection (WARNING)
  → [#144](https://github.com/jab16jy/techcamp-v2/issues/144), fixed as T7b's first commit. T7b also
  makes D13 unrepresentable: saving a row with a local `deleted_at` throws.
- T2 final fixes `432c74b` (`.get` → `InsufficientRoleError`, not `KeyError`) and `a73c1ba`
  (StrEnum message, no `getattr` fallback), both `Refs #143`, gated green (44 passed); merged into
  integration as `a463a02` (tests/logbook 63 passed, mypy clean). Review of both rides in T3's first
  slice.
- T3 (OpenCode `e8-t3`, Space Bunny **xhigh**, lane `e8-t3`, DB 5444) started from `a463a02`.
- T5 (AGY `e8-t5`) → `bfd8209` feat(logbook): visits read API, 847 lines. Writer: 25 passed, static
  clean, CodeGraph ×5. Parent gate (DB 5443): same; the 2 pytest warnings are Starlette/anyio
  deprecations inside `fastapi.testclient` (third party). Merged as `1783f20` with an add/add
  conflict on `logbook/domain` (T5 branched before T2): T2's files kept as base; T5's export check
  gets its own `VisitExportForbiddenError(role)` instead of a second `InsufficientRoleError`;
  integration 69 passed, static clean. **Order slip:** merged before its RDD; the lane RDD
  (`review-20cf11a1d3fa8cf3`, base `f242508`, covers `08dd5ef` + `bfd8209`, consent granted) runs
  now and any finding is fixed on integration.
- T5 RDD `review-20cf11a1d3fa8cf3` → **approved**, acknowledged; #145 (export paging test WARNING
  fixed by `10d0fff`, test-only, merged `84ac62d`; trailing-empty-page is a pre-existing
  codebase convention in farms/alerts routers; inverted date range SUGGESTION stays).
- T7b (`61f0d48` #144 fix, `e99e641` D13 guard, `92f439b` synchronizer run, `3f25562` triggers +
  pendingCount; 1,212 lines): writer ran 10 mutations, all caught (found two false-green tests).
  Parent gate: lint, typecheck clean; db+sync 5 files / 29 passed; build ok; 163.37 kB. OpenCode RDD
  running (base `182420b`, consent granted in the pane by the parent under the standing grant).
- T3 (owner asked why slow, 2026-09-28): `xhigh`, hardest unit, long debug of the D1 lock-ordering
  test hanging, and no commit yet despite the brief; nudged to land functional commits, drop the
  repro file and DBG prints, and make the ordering test deterministic with a timeout guard.
- T7b RDD (OpenCode, consent granted by the parent under the standing grant): reviewer found
  **CRITICAL R3-stale-push-result-settles-newer-write** (a push result settled by `id` alone
  deleted an edit made while the request was in flight — RNF-01); fixed in the bounded correction
  `d105968` (+62: settle only if the outbox auto-increment key is the one sent, inside the settling
  transaction), validator approved, acknowledged. Known gap: no test proves the check is inside the
  transaction (3/4 correction mutations caught). Parent gate on `d105968`: lint, typecheck, 31
  passed. Three WARNINGs → [#147](https://github.com/jab16jy/techcamp-v2/issues/147) (invalid JSON
  escapes as unhandled rejection; single-flight drops the 2 s follow-up; unknown pull entity written
  into `extensionVisits` — fail-open, same lesson as T2), being fixed now by `e8-t7`, reviewed with
  T8's first slice. T7 lane then merges into integration.
- T3 landed `fef63b0` (push apply: D1 lock + savepoint per change) and `030528e` (`POST /sync/push`,
  batch limit); concurrency tests commit pending.
- T6 started (owner approved running it in parallel): OpenCode `e8-t6` high, lane `e8-t6`
  (`feat/e8-t6-presign`, DB 5445), new modules only to avoid clashing with T3's ports/repositories.

- T3 gate (parent, DB 5444) on `fef63b0`, `030528e`, `cf4973e` (1,485 + 642 + 112 lines; over the
  ~650 forecast, 17 push-table behaviour tests): 94 passed, ruff, format (265), mypy (177),
  lint-imports clean. Docs diff sent back before RDD: (1) `created_offline` hard-coded `True` on
  insert breaks docs/11 "Uso offline" (the T7 client already sends `!navigator.onLine` in `data`,
  docs/04 "data lleva los campos de logbook_entry"): take it from `data` on insert, never change it
  on update; (2) `op: delete` still checks `crop_cycle_id`/`alert_id`, so a change rejected
  `not_found` for them can never be deleted (D12 "no valida los campos", RNF-01): keep plot/org/role
  checks, skip reference checks; (3) duplicated delete branch in `_apply_visit`. Writer judgment
  call accepted: an unknown `kind` is that change's `rejected invalid`, not a batch `422` (D5).

- T3 gate file: the first send pointed at a scratchpad file whose write had failed; the writer
  stopped instead of guessing (correct). Findings re-sent as `e8-t3/.git-brief-e8-t3-gate.md`.
- T7 #147 fixes `58fb730` (unknown pull entity fails closed, page aborts, cursor holds),
  `27ce906` (non-JSON 2xx → `unavailable`; triggers go through one catching `startRun`),
  `6d73169` (one follow-up run for a write during an in-flight run). Parent gate: lint, typecheck
  clean; db+sync 5 files / 35 passed; build ok; 163.37 kB. Accepted: a page the client cannot
  parse blocks the cursor (T8 should show "sync blocked"); `console.error` in `startRun`. Quality
  issue sent back: the `startSynchronizer` JSDoc was left orphaned above `startRun`. Review of the
  three fixes rides in T8's first slice (base `d105968`).
- T6 `baa2545` deps boto3, `9efe910` presign, `0b19fd3` MinIO bucket init + S3 settings (1,339
  lines incl. `uv.lock`). Parent gate (DB 5445): attachments tests 15 passed, ruff, format (270),
  mypy (181), lint-imports clean. Accepted: validate before any query, 404 non-member / 403 role,
  sign before insert, production S3 keys without defaults (503), `minio/mc:latest` with a pin-later
  note. Gap sent back: the presigned PUT does not sign the length, so D8's 200 KB is only checked
  on the declared `bytes`: sign `ContentLength` (or report if botocore cannot).

- T7 `ddb46bc` (JSDoc back on `startSynchronizer`, 14/14 pure move) gated: lint, typecheck,
  35 passed. **T7 merged** into integration (`feat/e8-t7-sync`, lane RDD `review-fb0c138492a9057a`
  + `review-6bc080d05399a4ad` approved); integration web: lint, typecheck, db+sync 35 passed,
  build, 163.37 kB. `e8-t7` session closed. The #147 fixes (`58fb730..ddb46bc`) are reviewed with
  T8's first slice (base `d105968`).
- T3 fixes `3c8ff85` (`created_offline` from `data` on insert, kept on update), `0393bb6` (delete
  skips cycle/alert and the visit's plot-in-farm checks; visibility, org, role, technician_id
  stay), `b28adf4` (one `_apply_delete`). Gate (DB 5444): push + sync_api 34 passed, static clean.
  Go given for OpenCode's own RDD (base `a463a02`); consent answered by the parent in the pane.
- T6 `887e944` (signs `ContentLength`; UNSIGNED-PAYLOAD, so the ceiling binds the declared length).
  Gate (DB 5445): 16 passed, static clean. **T9b obligation:** the browser's Content-Length must
  equal the presigned `bytes` (never re-encode after presign). Go given for OpenCode's own RDD
  (base `3cf2653`).

- T6 RDD (OpenCode, consent granted by the parent under the standing grant): lineage
  `review-9ed2f09b8ef0ddd5`, lens `review-reliability`, one **CRITICAL R3-MINIO-STARTUP-RACE**
  (short `depends_on` waits for MinIO to start, not to serve; `api` waits on
  `service_completed_successfully`, so one early `mc` failure kept the stack down) fixed in the
  bounded correction `f0d484c` (bounded 10× retry, 22 lines), validator approved, acknowledged. No
  non-blocking findings. Open design note (not a finding): `api` still hard-depends on
  `minio-init`, so a broken MinIO keeps the API down although presign alone would 503.
  **T6 merged** into integration: tests/logbook 85 passed, ruff, format (270), mypy (181),
  lint-imports clean. `e8-t6` session closed.

- T3 RDD (OpenCode, consent granted by the parent under the standing grant): lineage
  `review-7f77739c188c70fd`, slice `fef63b0..b28adf4` (base `a463a02`; T2's `432c74b`, `a73c1ba`
  sit under the base), medium, 2,369 lines, lens `review-reliability` → **approved**, acknowledged,
  no blocking findings, no correction. Three test WARNINGs →
  [#160](https://github.com/jab16jy/techcamp-v2/issues/160): R3-lock-test-does-not-prove-allocation-order
  and R3-newer-update-content-unproved are fixed now by `e8-t3` (test-only, `Refs #160`, no new
  review); R3-db-backstop-bypasses-push-contract stays in the issue. Then T3 merges (expect
  `main.py` and `logbook/adapters/api/deps.py`, `ports.py`, `repositories.py` conflicts with T5/T6).

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
- 2026-09-28 Owner: run independent tasks in parallel, sequential ones in order. Lanes now: T1
  (AGY `e8-t1`, schema) ‖ T2 (AGY `e8-t2`, pure domain, `logbook/domain` only) ‖ T7 (OpenCode
  `e8-t7`, `web/lib/{db,sync}` only). They share no files; T5 waits for T1, T3 for T1+T2.
  Corrected the same hour (owner: keep lanes separate): the conftest autouse fixture downgrades to
  `base` at the end of every pytest run, so shared lanes would drop each other's tables. T2 and T7
  were stopped before writing anything and restarted in their own worktrees: `e8-t2`
  (`feat/e8-t2-domain`, DB `techcamp-e8-db-t2` on 5442) and `e8-t7` (`feat/e8-t7-sync`, web, no
  DB), each with its own CodeGraph index; T1 stays in `e8-logbook`. The parent merges each lane
  branch into `feat/e8-logbook` after its gate and RDD.
  D11 added (no refresh endpoint in seminar).
- 2026-09-28 T1 (AGY `e8-t1`, Gemini 3.8 Flash medium) → `f242508` feat(logbook): schema, 693
  lines (prod 367 / tests 326). Writer evidence: RED `ModuleNotFoundError techcamp.logbook.adapters.orm`,
  GREEN 17 passed; CodeGraph `explore "AlertRow"`, `explore "uuid7"` (after the parent's nudge).
  Parent gate on the sha (DB 5441): `pytest tests/logbook` 17 passed; ruff check clean; format 254
  files; mypy 169 files clean; lint-imports 1 kept 0 broken. Docs diff: migration matches docs/03
  columns, D1 shared sequence default, D6 nullable cycle, every D10 CHECK, topics CHECK, attachment
  exactly-one; `sold_kg <= yield_kg` is null-safe because harvest-only + harvest-requires-yield.
  RDD: assess medium (`executable_change` env.py), due; consent granted (standing new-feature
  grant); lineage `review-b3312c6f95cbe5ea`, one lens `review-reliability` — running.
- 2026-09-28 T2 (AGY `e8-t2`) → `f1cbbaf` feat(logbook): pure domain, 808 lines (prod 302 / tests
  506). Writer evidence: RED `ModuleNotFoundError techcamp.logbook.domain.errors`, GREEN 42 passed;
  CodeGraph MCP explore ×3 + `node Role`, `query tzinfo`. Parent gate (DB 5442): 42 passed; ruff,
  format (255), mypy (170), lint-imports clean. Docs diff: D3 roles, D4 branches incl. tie, D5
  strict > 24 h, D10 rules all match. Quality issues sent back (fix now, new commit):
  self-paired `_NUMERIC_AMOUNT_FIELDS`, `Sequence[str | VisitTopic]` union, unreachable
  `ValueError` fallback in `ensure_can_sync`. RDD after the fix commit.
- 2026-09-28 T7 (OpenCode `e8-t7`, Space Bunny high) → `fe7fb09` chore(deps): dexie ^4.4.6,
  fake-indexeddb ^6.2.5 (20 lines). Parent gate: lint, typecheck clean; size 163.38 kB / 200 kB
  (unchanged: Dexie is not in the entry chunk). No RDD on deps alone: its slice is fe7fb09 + T7a. AGY T1 ignored CodeGraph beyond `status`; the common
  brief now mandates the CLI and lists the commands as report evidence.

## Next step
(2026-09-28, session handoff) Integration `feat/e8-logbook` holds T0, T1, T2, T5 (69 logbook tests).
1. T7 (`e8-t7`): gate the #147 fix commits → merge `feat/e8-t7-sync` into integration.
2. T3 (`e8-t3`): wait for the concurrency-tests commit → parent gate (DB 5444) → go for OpenCode's
   own RDD (base `a463a02`; the slice also covers T2's `432c74b`, `a73c1ba`) → merge. Expect an
   add/add conflict on `logbook/application/ports.py` and `logbook/adapters/repositories.py`
   (T3 branched before T5): combine both, like the T5 merge (`1783f20`).
3. T6 (`e8-t6`): gate → go for its RDD → merge.
4. Then T4 pull (after T3), T8 logbook screen (after T3/T4/T7; first commit regenerates
   `schema.d.ts` and swaps the T7 transport to `apiClient`, calls `requestPersistentStorage()` on
   start), T9 visits screen + photos, T10 close with the only full-suite run.
