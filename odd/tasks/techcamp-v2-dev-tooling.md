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
- D-T1.1 The database identity is the worktree path through POSIX `cksum`: port
  `55000 + crc % 10000`, container `techcamp-db-<slug>-<crc>`. Every worktree follows the rule,
  the main checkout included, so the seminar stack on 5432 is never what these recipes touch and
  the isolation invariant has no exception to remember. A taken port fails `db-up` loudly rather
  than sharing a database. Rejected: keeping the main checkout on the compose DB (two rules, one
  of them a carve-out from the invariant this task exists to remove).
- D-T1.2 The identity is derived in a private `db-env` recipe, not in a backtick: `just` does not
  interpolate `{{_worktree}}` inside a backtick string, so a backtick hashed the literal text
  `{{_worktree}}` and every worktree resolved to the same container and port. Found by running
  `db-info` from a second path, not by reading the docs.
- D-T1.3 `gate-lane` checks each commit out in a detached temporary worktree and judges it against
  its own dependency graph: uv builds that commit's own `.venv` from its own `uv.lock`, and
  `web/node_modules` is symlinked from the lane root only while the two agree on
  `web/package-lock.json`, installed with `npm ci` in the checkout when they do not. Symlinking it
  unconditionally was `R3-lane-node-modules`, fixed in the RDD correction `001e7cb`: a borrowed
  graph is E8 PR #182's shape, a commit gated against something other than its own tree. No commit
  is rewritten and the lane's tree never moves, so the shas it prints are the branch's shas.
  Rejected: `git rebase -x` (rewrites shas) and checking out in place (leaves the tree on another
  commit when the run stops at a failure).
- D-T1.4 `gate` exports a `DATABASE_URL` derived from this worktree and ignores an inherited one: a
  URL left in the environment by another checkout would point these tests at a database another
  worktree migrates and drops. The escape hatch is running pytest directly.
- D-T1.5 `just --working-directory` must be paired with `--justfile` (`just --help`, 1.58.0); the
  ctx7 quick reference shows `-d` alone. Caught by the gate-lane evidence run, not by a doc.
- D-T1.6 The single-Alembic-head check joins the CI server job, in T2 (owner call, 2026-09-29).
  T2 already edits `ci.yml` for the ast-grep scan, so the head check lands in that work unit and
  T1 does not touch CI. Until T2 lands, `gate-fast` is the only place the check runs; after it,
  CI and `gate-fast` list the same static checks.
- D-T2.1 The dev dependency is `ast-grep-cli`: it ships the `ast-grep` binary as a wheel script,
  while `ast-grep-py` is the library binding and installs no executable. The venv also gets an
  `sg` from the same wheel, while `sg` in a shell is the system switch-group command, so every
  recipe and CI step calls `ast-grep` and never `sg`. The version is frozen by `uv.lock`
  (0.45.3) like every other dev dependency and CI syncs with `uv sync --locked`; an exact `==` pin
  would diverge from the file's own convention without adding reproducibility the lock does not
  already give.
- D-T2.2 `no-naive-today` covers the three shapes the docs forbid — `date.today()`, a zero-argument
  `datetime.now()` and `datetime.utcnow()` — scoped with `files: server/src/**/*.py` and
  `ignores: server/src/techcamp/shared/dates.py`, the one module allowed a naive read because it
  attaches UTC to it on the next line. Recorded limit: `datetime.date.today()` (a three-segment
  member chain) and `datetime.now(tz=None)` are not matched. A metavariable form (`$X.today()`)
  would catch the first but would also flag any domain method named `today()`, so the precise
  shapes stand and a second rule id can add more when a real occurrence demands it. The rule test
  documents both what is covered and what is not.
- D-T2.3 The scan is one command, run from `server/` in both places: `uv run ast-grep scan
  --config ../sgconfig.yml`. Rule paths are relative to the config file, not the cwd, so the scan
  covers the whole tree whatever a rule names, and CI's server job (whose working directory is
  `server`) runs the identical line. CI also runs `uv run ast-grep test`, so a rule that stops
  reporting what it claims fails the build instead of passing silently.
- D-T2.4 D-T1.6 lands with T2: the CI server job asserts exactly one Alembic head, spelled out as
  the recipe spells it, because CI calls the tools directly and never `just`. That closes
  `R3-stale-ci-parity` (#193) without touching the AGENTS.md sentence, which is now true.

## Tasks
- [x] T1 `justfile`: `db-up`/`db-down`/`db-reset` per worktree, `gate-fast` (incl. single
  Alembic head), `gate *paths`, `gate-full`, `gate-lane base`. AGENTS.md Commands + the
  `D-Tx.n` convention in Workflow. Evidence: each recipe run, `gate-lane` over a range with
  one broken commit (in a scratch branch, deleted after).
- [x] T2 ast-grep: pinned dev dep, `sgconfig.yml`, rule `no-naive-today` + rule tests (RED
  first), wired into `gate-fast` and CI.
- [ ] T3 pytest-randomly: dev dep; full suite on a clean DB with 3 seeds; flakes reported on
  #89 with seeds; fix only trivial ones.
- [ ] T4 `alembic check` trial on a clean DB: adopt (with a small `include_object` filter if
  needed) into `gate-full` and CI, or reject with evidence in #139.
- [ ] T5 gitleaks in CI (docs/09:78): verify the action/binary needs no license for this
  personal repo; run it locally once over the history.
- [ ] T5b CI cost: `concurrency` (group per workflow + PR ref, `cancel-in-progress: true`) on
  every PR workflow; per-JOB path filter (never workflow-level `paths:`, which leaves required
  checks pending): server job on `server/**`, `infra/**`, `.github/workflows/**`; web job on
  `web/**`, `.github/workflows/**`; ast-grep and gitleaks always. Skipped jobs must report
  success. Evidence: a web-only and a docs-only change skip the server job (act or a draft PR
  run, owner go needed for a push). Data: last 40 PRs, 24 server-only, 13 web-only; server
  job ~7 min, web ~2 min; repo private (2000 min/month free plan).
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
- T1 is done. Round 1, lineage `review-429366d35a8ea6b3`: candidate `9519e05..HEAD` (3 files, 485
  lines, `medium`), one lens (`review-reliability`), one correction budget. One CRITICAL,
  `R3-lane-node-modules`, fixed in the single bounded correction `001e7cb`; approved and
  acknowledged, `authority: burned`. The two WARNINGs (`R3-stale-ci-parity`,
  `R3-unescaped-worktree-path`) and the D-T1.3 drift are #193; the unescaped path and the doc drift
  are fixed in this session, and `R3-stale-ci-parity` is accepted until T2 lands D-T1.6 in this PR.

## Progress / evidence
- 2026-09-29 T0: research done (Engram #290), scope approved by the owner, worktree
  `techcamp-v2-worktrees/dev-tooling` on `feat/dev-tooling` from `9519e05`, CodeGraph indexed.
- 2026-09-29 T1: `justfile` at the repo root (`default`, `db-info`, `db-up`, `db-down`,
  `db-reset`, `gate-fast`, `gate *paths`, `gate-full`, `gate-lane base="main"`, private
  `db-env`) + AGENTS.md Commands and the Workflow decision convention. `just` 1.58.0 installed
  for the runs via `uv tool install rust-just`. Every recipe run on this worktree:
  - `just db-info` → `techcamp-db-dev-tooling-2722977105` on port 62105; the same justfile at
    another path → `techcamp-db-other-wt-2302701857` on 56857, and the main checkout's path →
    `techcamp-db-techcamp-v2-1942765054` on 60054. Three paths, three identities.
  - `just db-up` → ready in ~7s; `postgis`, `timescaledb` and `vector` present in the container,
    so the mounted `infra/postgres/init-extensions.sql` ran. Second `just db-up` → "already
    exists; run 'just db-reset'", exit 1.
  - `just gate-fast` → ruff, ruff format, mypy (186 files), lint-imports, one Alembic head
    (`e8b109b00c01`), eslint, tsc: green in 22s. Negative case: an untracked scratch file with a
    formatting error → `1 file would be reformatted`, exit 1, file deleted again.
  - `just gate server/tests/shared/test_dates.py` → static checks + `4 passed`. A node id
    (`…::test_bogota_tz_is_america_bogota`) → `1 passed`; `web/src/design-system/components/
    MetricTile.test.tsx` → `3 passed`; `docs/09-cuellos-de-botella.md` → "no test runner; the
    static checks already ran". A path with no tests → pytest exit 4, the recipe fails instead of
    passing quietly. Bare `just gate` → the eight static steps and no test.
  - `just gate-full` and `just gate-lane fdb248e` on a scratch branch (`scratch/gate-lane-t1`,
  three commits: justfile, then a commit importing `techcamp.shared.not_yet` that the next commit
  adds — the E8 PR #182 shape — then that module). The lane's HEAD passed `gate-fast`
  ("Success: no issues found in 186 source files"), so gating HEAD alone would have passed;
  `gate-lane` passed `31b1824`, stopped at `e266c06` with the mypy failure and named it, and never
  reached the fixing commit. Exit 1. The main worktree stayed on `fdb248e` with an untracked
  `justfile` for the whole run, the scratch shas were unchanged, and the scratch branch and both
  temporary worktrees were removed after.
  - Not run here: `gate-full`'s full pytest/vitest/build/size (T3 and T6 by design, the full
    suite once on a clean DB at epic close).
  - Known gap, closed in T2 by D-T1.6: the single-Alembic-head check is in `gate-fast` and not
    yet in CI, the one check the two did not share. T1 does not touch CI (the scope adds
    ast-grep, gitleaks and whatever T4 adopts, and nothing else); T2 adds `alembic heads` to the
    server job in the same work unit as the ast-grep scan, in the same PR. The RDD WARNING
    `R3-stale-ci-parity` on the AGENTS.md sentence that already claims parity is therefore
    accepted for this round and AGENTS.md is left as is: T2 makes the sentence true in the same
    PR that ships the rest of the feature, and softening it now would only be true twice.

  - Known gap, closed in T2 by D-T1.6: the single-Alembic-head check is in `gate-fast` and not
    yet in CI, the one check the two did not share. T1 does not touch CI (the scope adds
    ast-grep, gitleaks and whatever T4 adopts, and nothing else); T2 adds `alembic heads` to the
    server job in the same work unit as the ast-grep scan, in the same PR. The RDD WARNING
    `R3-stale-ci-parity` on the AGENTS.md sentence that already claims parity is therefore
    accepted for this round and AGENTS.md is left as is: T2 makes the sentence true in the same
    PR that ships the rest of the feature, and softening it now would only be true twice.
- 2026-09-29 T2: `ast-grep-cli` in the server dev group (locked at 0.45.3), `sgconfig.yml` at the
  root, `rules/no-naive-today.yml`, `rule-tests/no-naive-today-test.yml` with its generated
  snapshot, the scan in `gate-fast` and in the CI server job, the rule tests in CI, and D-T1.6's
  single-Alembic-head assertion in the same job. One commit, so CI and `gate-fast` list the same
  checks at every commit of the lane.
  - RED, rule matching only `date.today()`: `[Missing] Expect rule no-naive-today to report
    issues, but none found in: now = datetime.now()` and the same for `datetime.utcnow()`;
    `FAIL no-naive-today  ......WMM` / `test failed. 0 passed; 1 failed` (exit 4). The first RED
    was structural: `Error: Cannot read rule directory .../rules` (exit 6).
  - GREEN: `PASS no-naive-today  .........` / `test result: ok. 1 passed; 0 failed` (exit 0). The
    first all-three-shapes run failed with `Test failed due to mismatching snapshots`, so the
    baseline was generated with `ast-grep test --update-all` and committed.
  - Six `valid` cases (including `datetime.now(UTC)`, `datetime.now(tz=UTC)`,
    `datetime.now(BOGOTA_TZ)` and `local_today(...)`) and three `invalid` ones.
  - Negative controls: a scratch `date.today()` in `server/src` is reported from the repo root and
    from `server/ --config ../sgconfig.yml` alike, and `just gate-fast` then exits 1 with the
    rule's message; the same read inside `server/src/techcamp/shared/dates.py` is not reported
    (file held the violation at line 34 while the scan exited 0), which is what `ignores` is for.
    Both scratch edits were reverted and `git status` confirmed the tree.
  - The existing tree is clean under the rule: 30-odd `datetime.now(UTC)` calls and zero
    `date.today()`, zero-arg `datetime.now()` or `datetime.utcnow()` in `server/src`.

## Next step
T2 committed; the parent gates the sha, then the RDD review runs for the slice. Next task: T3
(pytest-randomly: dev dep, full suite on a clean DB with 3 seeds, flakes on #89), after the
parent's go.
