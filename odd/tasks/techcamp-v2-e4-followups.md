# TechCamp v2 — E4 Review Follow-ups

## Objective
Resolve the non-blocking RDD findings of epic E4, tracked in issues #34–#44 (48 findings: 7 server issues, 4 web issues), and close each issue with a `Refs #N` / `Closes #N` commit.

## Problem
E4 shipped to `main` (7f2f290, PRs #45–#57) with WARNING/SUGGESTION findings deferred to the tracker (AGENTS.md Workflow). Several are real robustness bugs: a bad `ts` aborts an ingest batch, `last_seen_at` can go backwards, the SSE hub dies on a Postgres error, recalibration jobs can race, and the web list truncates past one page.

## Why
E6 (irrigation) and E7 (alerts) consume calibrated readings and the live stream. Fixing these before building on top keeps the defects from spreading into the water balance and alert rules.

## Scope
- In: every checkbox finding in #34–#44.
- Out (the issues call them doc/server gaps, not findings; they need docs first and stay open as notes): `reading` event without `sensor_id`/`depth_cm` (#44), `Last-Event-ID` without replay and per-process event ids (#44), hook connection state (#44), one stream per plot sheet (#44), no read endpoint for a sensor's calibrations (#43), out-of-range rule only for `%` units (#34).
- Out: E0/E1/E2 follow-ups (#5, #12, #7), owner decision 2026-09-25.

## Constraints
- AGENTS.md: docs win; ADR-0002 hexagonal + import-linter; every repository filters by `org_id` (docs/09); English code, Spanish UI copy only where docs/07 requires it; E1 design frozen (`impeccable`, no visual changes).
- Ponytail: shortest correct diff; no new dependencies.
- A fix that changes documented behavior updates the owning doc in the same commit (`domain-modeling` for ADR/glossary).

## Route and checks
- TDD: on (owner decision 2026-09-22, AGENTS.md), run by ODD: RED → GREEN → REFACTOR, RED quoted in the report. Runners: `uv run pytest` (server/), `npm test -- --run` (web/).
- Other checks: server `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run lint-imports`; web `npm run lint`, `npm run typecheck`, `npm run build`, `npm run size`.
- Route: delegated direct via Herdr, two chains in parallel on separate worktrees (file sets are disjoint):
  - Server chain: OpenCode writer, which also runs RDD on its own work-unit commits (supported runtime).
  - Web chain: AGY (Antigravity) writer. Verified 2026-09-25: `gentle-ai review status --agent antigravity` fails with `immutable_review_transport_unsupported`, so AGY cannot run RDD; the parent (Claude Code) runs RDD on the web commits.
  - Herdr sessions: a fresh session per task (memory `herdr-delegation-sessions`).
- Skills forwarded: `fastapi`, `pydantic`, `find-docs`, `work-unit-commits`, `systematic-debugging`; web also `impeccable`.
- RDD: on (global). One `gentle-ai review assess --committed-only` per work-unit commit; standing grant on medium/high (memory `rdd-consent-default-granted-features`). Blocking findings are fixed in the bounded correction; new non-blocking findings go to one new issue per review round.
- Delivery: `stacked-to-main`, about 400 authored lines per PR, one chain per area. Forecast ≈ 2,400 authored lines (server ≈ 1,850, web ≈ 550).
  - Server: branch `fix/e4-followups-server` from `main` @ `7f2f290`, worktree `~/proyectos/techcamp-v2-worktrees/e4-followups-server`.
  - Web: branch `fix/e4-followups-web` from `main` @ `7f2f290`, worktree `~/proyectos/techcamp-v2-worktrees/e4-followups-web`.
  - This document is committed on `main`-based branch `fix/e4-followups-server` first; the web branch starts from that same commit.

## Decisions
- Unknown `metric` on `GET /plots/{id}/readings` (#37): reject with 422 (validation over silent empty result); docs/04 updated.
- 500-node cap (#37): document the cap in docs/04; no truncation field until a plot can realistically exceed it.
- Calibration version race (#35): on unique-violation return 409 `calibration_version_conflict`; the client retries.
- procrastinate (#39): pin the exact installed version in `pyproject.toml`; test `_split_sql_statements` against the real schema.
- Web list pagination (#41): `useNodes` follows `next_cursor` until exhausted (a plot has few nodes).
- Quality flag drift (#39, last finding): needs an owner doc decision (docs/03:174 stores two signals in one `quality`); asked when F5 starts.

## Tasks
Server chain (OpenCode, RDD by the writer):
- [ ] F1 Uplink parsing and ingest robustness — #34 (all 4) + #36 (all 4): `ts` range guard (malformed, not a crash), reject NaN/Infinity, strict `v` and non-dict payload, Timescale block repeat test; monotonic `last_seen_at`/status, `reading` events only for inserted rows, rename/fix the unclaimed-node test — forecast ~400
- [ ] F2 Node API and calibration — #35 (all 6): 409 on version race + concurrent test, validate params directly (422), `GET /nodes` pagination/filter tests, write-role 403 tests, lost claim race test, `verify_password` on malformed hashes — forecast ~450
- [ ] F3 Readings query and SSE hub — #37 (all 3) + #38 (all 3): tz-aware `from`/`to` (422), unknown metric 422, document the node cap; hub retries on `PostgresError` and closes a half-open connection, readiness flag for tests, subscribe inside the generator — forecast ~400
- [ ] F4 Recalibration jobs — #39 findings 1–5: pin procrastinate, splitter test, per-sensor job lock and `valid_from` tie-break, verify bucket alignment (T11b `82f09bc` fixed D5; close if proven), retry the refresh step — forecast ~300
- [ ] F5 Quality flag drift — #39 finding 6: owner doc decision first, then docs/03 + code — forecast ~150
- [ ] F6 Simulator — #40 (all 5): confirm calibration tests, live index offset after backfill, validate backfill args, one-transaction provisioning, `run()` orchestration test — forecast ~250

Web chain (AGY writer, RDD by the parent):
- [ ] W1 Nodes API, claim and rotate — #41 (all 3) + #42 (both) + #43 (all 3): follow `next_cursor`, `enabled` guards, org-null test, no concurrent scan leak, handle clipboard rejection once in `OneTimeSecret`, keep the rotate password when the sheet closes while pending, stronger no-rotate test — forecast ~300
- [ ] W2 Stream client — #44 (all 5): deterministic ignored-event test, reset backoff only after a received event, ticking freshness label, abort listener cleanup, id-less frame handling — forecast ~250

## Review (RDD)
- Server boundary: `7f2f290`. Web boundary: `7f2f290`.

## Acceptance criteria
- Every checkbox in #34–#44 is fixed with a test (or proven already fixed), and each issue is closed by a referencing commit or PR.
- Out-of-scope doc gaps remain listed in their issues.
- All server and web checks green; CI green on every PR.

## Progress / evidence
- 2026-09-25: feature doc created; scope E4 only (owner).

## Next step
1. Create the two worktrees and start F1 (OpenCode) and W1 (AGY) in parallel Herdr panes.
