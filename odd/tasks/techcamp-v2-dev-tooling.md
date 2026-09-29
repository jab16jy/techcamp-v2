# TechCamp v2 — Deterministic dev tooling (#139)

## Objective
Move what a deterministic tool can decide out of agent judgement and RDD rounds, before E9:
one local gate command with selectable pytest scope, a per-commit lane gate, a per-worktree
test database, executable invariants (ast-grep), order-randomized tests, and the CI checks
the docs already require.

## Why
Issue #139 (evidence from E7/E8):
- RDD caught regressions of rules the docs already state (America/Bogota product day,
  docs/06 §5; `shared/dates.py`).
- The parent assembles gates by hand per slice: targeted pytest + ruff + format + mypy +
  lint-imports on each committed sha; the full suite runs once, on a clean DB, at epic close.
- E8 PR #182 broke CI: a split lane was gated only at HEAD, and a middle commit imported a
  file the next commit added.
- Parallel worktrees need their own Postgres (the session fixture in `server/tests/conftest.py`
  migrates to head and downgrades to base, dropping tables under another run).
- Decision numbers collided across parallel branches (D24–D29 reused by three branches).
- Order-dependent flakes on the shared DB are unreproducible today (#89).
- Doc gap: docs/09-cuellos-de-botella.md:78 requires `gitleaks` in CI; `ci.yml` has none.

## Scope
- In:
  - `justfile` at the repo root:
    - `gate-fast`: static checks only, no DB: server `ruff check`, `ruff format --check`,
      `mypy`, `lint-imports`, single Alembic head, `ast-grep scan`; web `lint`, `typecheck`.
    - `gate *paths`: `gate-fast` plus the tests the caller names. Paths under `server/`
      run with pytest, paths under `web/` with `vitest --run`. Examples:
      `just gate server/tests/logbook`, `just gate server/tests/irrigation/test_x.py::test_y`,
      `just gate web/src/lib/sync`. No paths = `gate-fast` only (never the whole suite by
      accident).
    - `gate-full`: the epic-close run. Recreate this worktree's test DB clean, then
      `gate-fast`, full pytest, full vitest, web `build` and `size`.
    - `gate-lane base`: runs `gate-fast` on every commit in `base..HEAD`, oldest first,
      stopping at the first failure and naming the sha. Must not rewrite history.
    - `db-up` / `db-down` / `db-reset`: one Postgres container per worktree
      (`timescale/timescaledb-ha:pg16`, podman or docker), name and port derived from the
      worktree directory, so parallel worktrees never share a DB. The test recipes export the
      matching `DATABASE_URL`.
  - ast-grep, pinned as a dev dependency, `sgconfig.yml` + first rule `no-naive-today`
    (`date.today()`, `datetime.now()` without tz, `datetime.utcnow()` in `server/src` outside
    `shared/dates.py`), with `ast-grep test` rule tests. Wired into `gate-fast` and CI.
  - pytest-randomly in the server dev group; flakes it surfaces go to #89 with their seed.
  - `alembic check` (ORM ↔ migration drift) on trial: adopted into `gate-full` and CI only if
    clean or clean with a small `include_object` filter (Timescale, PostGIS, procrastinate
    objects); otherwise rejected with evidence in #139.
  - gitleaks step in CI (docs/09:78).
  - Decision-number convention: decisions are numbered per task (`D-T3.1`, `D-T9b.2`), so
    parallel branches cannot collide. Stated in AGENTS.md Workflow.
  - AGENTS.md Commands documents the recipes (it is the agents' command reference).
- Out:
  - pytest-alembic (the session fixture already runs head ↔ base every run; the single-head
    check is one `alembic heads` line).
  - Claude Code `Stop` hook running the gate (not selected by the owner; revisit later).
  - `CODEOWNERS` + ML harness hash test (ADR-0020:42): belongs to E10, `ml/` does not exist
    yet; tracked as its own issue.
  - Hypothesis (E9/E11), Schemathesis + Playwright (E16), promptfoo (E12), pandera (E10):
    adopt inside those epics.
  - Rejected with reasons in #139: semgrep, pre-commit/lefthook, dependency-cruiser,
    pytest-recording, deepeval, pytest-xdist (until per-worker DBs), squawk (E14).
  - Fixing the #89 teardown deadlock itself, unless a fix is trivial.
  - Changing CI triggers (PR-only stays, commit `ci/pr-only`).

## Constraints
- AGENTS.md: docs first; English identifiers and prose in code/docs; Conventional Commits,
  no AI attribution.
- Ponytail: no new dependency where a few shell lines do the job; the justfile stays readable
  (no clever templating). `just` itself is a system tool (not a project dependency); document
  how to install it.
- CI keeps calling the tools directly (no `just` in CI), so CI and the justfile must list the
  same checks; the close task diffs them.
- Library/tool APIs from current docs (`find-docs` / ctx7), not memory: just, ast-grep,
  pytest-randomly, alembic check, gitleaks.
- No destructive git in the writer (no `reset --hard`, `checkout -- .`, `clean`, `stash`,
  `git add -A`).

## Route and checks
- Route: delegated direct. Writer: OpenCode via Herdr (owner 2026-09-29), one session for the
  whole feature (T1–T5 build one tool set). Parent (Claude Opus) planned this doc, gates each
  commit, and relays RDD consent. Trigger evidence: 2+ non-trivial files (justfile, CI,
  pyproject, sgconfig + rules, AGENTS.md).
- TDD: on (AGENTS.md Testing, owner decision 2026-09-22). Runners: `ast-grep test` for rules
  (RED: rule test fails before the rule exists), `uv run pytest` for anything in Python.
  Recipes with no unit-test surface get functional checks: run each recipe and record output,
  including one deliberate failure (e.g. a naive `date.today()` scratch edit, reverted).
- Checks per task: the recipes themselves plus the existing static checks; targeted tests only.
  Full suite once, in T6, on a clean DB.
- Delivery: forecast ~250 authored lines, one PR, stacked-to-main. Push/PR need the owner's go.

## Decisions
- D-T0.1 `gate` without paths runs static checks only; the full suite is explicit
  (`gate-full`). Owner: targeted pytest per task, full suite on a clean DB at epic close.
- D-T0.2 Decision numbers are per task (`D-Tx.n`), starting with this doc.
- D-T0.3 CODEOWNERS/hash test deferred to E10 (ADR-0020:42; `ml/` absent).

## Tasks
- [ ] T1 `justfile`: `db-up`/`db-down`/`db-reset` per worktree, `gate-fast` (incl. single
  Alembic head), `gate *paths`, `gate-full`, `gate-lane base`. AGENTS.md Commands + the
  `D-Tx.n` convention in Workflow. Evidence: each recipe run, `gate-lane` over a range with
  one broken commit (in a scratch branch, deleted after).
- [ ] T2 ast-grep: pinned dev dep, `sgconfig.yml`, rule `no-naive-today` + rule tests (RED
  first), wired into `gate-fast` and CI.
- [ ] T3 pytest-randomly: dev dep; full suite on a clean DB with 3 seeds; flakes reported on
  #89 with seeds; fix only trivial ones.
- [ ] T4 `alembic check` trial on a clean DB: adopt (with a small `include_object` filter if
  needed) into `gate-full` and CI, or reject with evidence in #139.
- [ ] T5 gitleaks in CI (docs/09:78): verify the action/binary needs no license for this
  personal repo; run it locally once over the history.
- [ ] T6 Close: `just gate-full` on a clean DB, CI ↔ justfile check list diff, feature doc
  progress, #139 comment with results.

## Acceptance criteria
- `just gate server/tests/logbook` runs static checks plus only those tests, against this
  worktree's own DB.
- `just gate-lane main` stops at a broken middle commit and names it; history unchanged.
- A naive `date.today()` in `server/src` fails `gate-fast` and CI with the rule's message.
- Two worktrees can run `just gate …` at the same time without touching each other's DB.
- CI runs ast-grep, gitleaks and whatever T4 adopted; nothing else in CI changes.

## Review (RDD)
- Boundary: branch point `9519e05`. Per work-unit commit; the parent gates first, then the
  OpenCode session runs RDD. Non-blocking findings go to one issue per round
  (`review-follow-up`, `area:*`, `type:*`).

## Progress / evidence
- 2026-09-29 T0: research done (Engram #290), scope approved by the owner, worktree
  `techcamp-v2-worktrees/dev-tooling` on `feat/dev-tooling` from `9519e05`, CodeGraph indexed.

## Next step
T1 by the OpenCode writer.
