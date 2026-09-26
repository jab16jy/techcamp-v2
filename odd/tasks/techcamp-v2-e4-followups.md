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
- Quality flags (#39 finding 6, owner decision 2026-09-25): `reading.quality` stays a smallint read as flags — 1 timestamp corrected, 2 out of range, 3 both; ingest ORs them, recalibration keeps the timestamp flag and recomputes only the range flag; migration `a3f1c7d92b40` widens `ck_reading_quality` to 0..3 (downgrade folds 3 into 2). docs/03 + docs/06 §1 updated.
- Review follow-ups (owner, 2026-09-25): every WARNING/SUGGESTION from a writer's own review is filed as that round's issue and fixed right away by the same writer session, then reviewed again, until clean. Rounds opened and closed here: #58, #59, #60, #61, #62, #63.
- Parallel server writers (owner, 2026-09-25): F3–F6 ran as separate OpenCode sessions in Herdr tabs, each on its own branch from `ebd8495` and its own test database (`techcamp_f3..f6`), because the test session migrates up and down. Branches were cherry-picked into one linear chain (no conflicts) and re-verified before delivery.
- AGY (Antigravity) cannot run RDD: `gentle-ai review status --agent antigravity` → `immutable_review_transport_unsupported` (verified 2026-09-25). The parent ran RDD on the web commits as `claude-code`.

## Tasks
Server (OpenCode writers, each ran RDD on its own commits):
- [x] F1 Uplink parsing and ingest robustness — #34 + #36 — `aaa5d37` (294) + `ebd8495` (249) — review `review-e4215d6cd1963217` approved; its test gap → #58
- [x] F2 Node API and calibration — #35 — `c20a5a5` (476) — review `review-cf8c859dcd7d8813` approved; WARNING → #61
- [x] F7 F1/F2 review rounds — #58 + #61 — `947a8d7` (113) — review `review-0ebeb30d0606fb4d` approved, 0 findings
- [x] F3 Readings query and SSE hub — #37 + #38 — `f5fe9c9` (409) — review `review-78d44617dfdc5c5c` approved (CRITICAL refuted); WARNING + flaky aggregate tests + dead `is_subscribed` → #63, fixed by the same session in `e1fcf6d` (257) + bounded correction `70ac6ef` (18, CRITICAL `R3-stop-cancels-unclaimed-release`) — review `review-d5471db1c6563a1d` approved. Flake root cause: TimescaleDB background policies share a lock with the tests' manual refresh; conftest unschedules them for the session.
- [x] F4 Recalibration jobs — #39 findings 1–5 — `298a214` (230) + `5e59f4e` (290) — review `review-c053699db24e9666` approved, 0 findings. Bucket alignment needed no change (proven by the existing test).
- [x] F5 Quality flags — #39 finding 6 — `be82ebf` (191) + bounded correction `040d70b` (4, blocking: downgrade failed after a stored 3) — review `review-301f5e8a8348e0de` approved
- [x] F6 Simulator — #40 — `ceb81a2` (345; also fixed a sub-second calibration hole, noted on #40) — review `review-167599c1aee68072` (high, four lenses) approved; 2 WARNING → #62, fixed by the same session in `7a81c22` (229) — review `review-fb08d5bcba4a3a59` approved, 0 findings

Web (AGY writer, RDD by the parent):
- [x] W1 Nodes API, claim and rotate — #41 + #42 + #43 — `cd3160b` (256). Parent correction during the task: the one-time password is not cached (docs/04:83); the sheet ignores close while rotation is pending.
- [x] W2 Stream client — #44 — `56fc2bb` (166) — W1+W2 review `review-2184809b8004f415` approved; 4 findings → #59
- [x] W3 Web review round — #59 — `e40c032` (118, CodeGraph-first) — review `review-225b517f13a4827b` approved; 1 SUGGESTION → #60
- [x] W4 — #60 — `84fcb45` (9, parent, test proven to fail without the guard) — medium `under_budget`, not separately reviewed

## Review (RDD)
All lineages above approved and acknowledged (authority burned). Consent on every fix candidate was answered by the owner.

## Acceptance criteria
- [x] Every checkbox in #34–#44 fixed with a test or proven already fixed; every review round of this feature (#58–#63) fixed.
- [x] Out-of-scope doc gaps remain listed in their issues (#34, #43, #44).
- [x] Integration chain (`e4fu-int`): server 476 passed, ruff/format/mypy/lint-imports green, one Alembic head `a3f1c7d92b40`; web 190 passed, lint/typecheck/build green, size 162.39/200 kB. CI green on every PR (#64–#75) and on `main`.

## Progress / evidence
- 2026-09-25: feature doc created; scope E4 only (owner). All 11 tasks delivered by parallel Herdr writers; 3,535 authored lines over 18 commits (excluding `uv.lock`).

- 2026-09-26: delivered as stacked PRs #64–#73 (one per ODD task; F1, F2, F4 `size:exception`), merged in order with CI green; #34–#44 and #58–#63 closed.
- 2026-09-26: E4 end-to-end demo re-run on merged `main` with fresh volumes (compose project `e4demo`): all 8 E4 criteria plus 4 follow-up checks PASS (migrations at `a3f1c7d92b40`, procrastinate 3.10.0; 0 of 62 simulated readings uncalibrated; quality values within 0..3 and recalibration jobs `succeeded`; SSE kept serving and reconnected through a Postgres restart). It found three defects outside the unit tests' reach, fixed and merged:
  - D7 `web/Dockerfile:4` — the image did not copy `web/.npmrc` (`legacy-peer-deps`), so `npm ci` failed with ERESOLVE and the seminar `web` service never built; CI passed because it runs `npm ci` in the full checkout. PR #74. (Merged before its CI had registered; CI then passed on the PR and on `main`.)
  - D8 `infra/compose.yaml:108` — the web container proxied `/api/v1` to its own `localhost:8000` (502 on every call); default is now `http://api:8000`. PR #75.
  - D9 `PlotDetailSheet.tsx:466,474` — soil moisture printed the raw float; one decimal now, like `formatPercent`. PR #75.
- 2026-09-26: cleanup (owner): `e4-followups-*` worktrees, their local and remote branches, test databases `techcamp_f3..f6`, and the demo images and volumes removed. Only `main` remains.

## Next step
None: feature closed. `main` @ `8eb5dc0` is green.
