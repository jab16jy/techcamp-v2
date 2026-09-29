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
  - `alembic check` (ORM ↔ migration drift) on trial: **deferred to #194** by the owner
    (2026-09-29). The single-Alembic-head check it would have joined is already in `gate-fast`
    and in CI (D-T1.6, D-T2.4); the drift question the trial was meant to answer is #194's.

## Constraints
- AGENTS.md: docs first; English identifiers and prose in code/docs; Conventional Commits,
  no AI attribution.
- Ponytail: no new dependency where a few shell lines do the job; the justfile stays readable
  (no clever templating). `just` itself is a system tool (not a project dependency); document
  how to install it.
- CI keeps calling the tools directly (no `just` in CI), so CI and the justfile must list the
  same checks; the close task diffs them.
- Library/tool APIs from current docs (`find-docs` / ctx7), not memory: just, ast-grep,
  pytest-randomly, gitleaks.
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
- D-T2.2 `no-naive-today` covers the shapes the docs forbid — `date.today()`, a naive
  `datetime.now()` and `datetime.utcnow()` — under **both** import styles this repository uses:
  `from datetime import date, datetime` and the module-qualified `import datetime`, where the same
  shapes are `datetime.date.today()`, `datetime.datetime.now()` and `datetime.datetime.utcnow()`.
  The qualified spellings are not redundant with the direct ones: the member chain is a segment
  longer, so a direct pattern cannot see them, and 14 files in `server/src` use `import datetime`.
  A naive `now()` is either **no argument at all or an explicit `None` timezone** (keyword or
  positional), because `tz=None` returns a naive datetime and is the way to reach the same defect
  while looking deliberate; a call with a real timezone is valid and needs no `not` clause, since
  each pattern matches the exact shape rather than a prefix. The one recorded limit is an aliased
  import (`from datetime import datetime as dt`, then `dt.now()`), which is a different name and
  is not matched; it is accepted because every datetime import in `server/src` and
  `server/tests` is either `import datetime` or a plain `from datetime import ...` (checked
  2026-09-29), and it is written in the rule's own `notes`, where a developer editing the rule
  reads it. A metavariable form (`$X.today()`) would cover the aliases too but would also flag
  any domain method named `today()`, so the precise shapes stand. The rule test documents what is
  covered and what is not.
- D-T2.6 The rule has **no `ignores` entry**. `shared/dates.py` is where the timezone is owned
  and it never needed an exemption: its only read is `datetime.now(UTC)`, which is aware, and the
  naive value it handles arrives as an argument (`now.replace(tzinfo=UTC)`), not as a call. The
  file-level ignore added in T2 shielded every naive read a future edit put in that file, which
  is the opposite of the invariant (R3-blanket-timezone-ignore). Suppress a line with
  `# ast-grep-ignore` when a line genuinely needs it, never a file.
- D-T2.3 The scan is one command, run from `server/` in both places: `uv run ast-grep scan
  --config ../sgconfig.yml`. Rule paths are relative to the config file, not the cwd, so the scan
  covers the whole tree whatever a rule names, and CI's server job (whose working directory is
  `server`) runs the identical line. CI also runs `uv run ast-grep test`, so a rule that stops
  reporting what it claims fails the build instead of passing silently.
- D-T2.4 D-T1.6 lands with T2: the CI server job asserts exactly one Alembic head, spelled out as
  the recipe spells it, because CI calls the tools directly and never `just`. That closes
  `R3-stale-ci-parity` (#193) without touching the AGENTS.md sentence, which is now true.
- D-T2.5 T4 is deferred by the owner (2026-09-29) and tracked in #194. The `alembic check` ORM ↔
  migration drift trial leaves this feature's Scope, its CI acceptance criterion and the task
  list; the only migration check it could have joined — the single Alembic head — already landed
  in T2 (D-T1.6, D-T2.4), so nothing this feature promised is left unbuilt. The risk the trial
  was scoped around (Timescale, PostGIS and procrastinate objects reported as drift) is real
  and still unanswered, so #194 keeps the question with the evidence behind it.
- D-T2.7 `ast-grep test` joins `ast-grep scan` in `gate-fast`, so CI's server job is exactly
  `gate-fast`'s seven server checks plus the full pytest run and the parity sentence in AGENTS.md
  can name all seven instead of asserting parity (R2-ci-gate-parity-ambiguous). Both commands name
  `../sgconfig.yml` explicitly rather than relying on ast-grep's upward search for it, in the
  justfile and in CI alike, because a rule's `files` and `ignores` resolve against the config file
  and not against the working directory (R2-astgrep-wrong-working-directory).
- D-T3.1 A random seed is a **session** knob, not a property of a test. `--randomly-seed=N` runs
  the whole session in that order, and it is the only knob that proves anything about order
  dependence. The two debugging flags are not interchangeable, which is the part worth writing
  down: `-p no:randomly` disables the plugin entirely and therefore also drops the per-test
  `random.seed()` reset, so code that relies on that reset changes behaviour under it;
  `--randomly-dont-reorganize` keeps the per-test reset and only pins the file order. Reach for
  the second while debugging one test and the first only when the plugin itself is in question.

- D-T5.1 CI runs the pinned **gitleaks binary**, not `gitleaks/gitleaks-action`, even though
  the action needs no licence key on this personal account (its own README: "If you are
  scanning repos that belong to a personal account, then no license key is required";
  `gh repo view` confirms `jab16jy/techcamp-v2` is PRIVATE under a `User` owner). Two reasons
  the action was not used anyway. The action is licensed by Gitleaks LLC and stopped being MIT
  at v2.0.0, while the tool it wraps is MIT; every other CI tool here is permissive, and a
  proprietary wrapper buys nothing docs/09:78 asks for. And the action decides whether a key
  is needed by calling `GET /users/{username}`: on `type == "User"` it proceeds, but the
  `.catch` on that request leaves `shouldValidate` true and `process.exit(1)` prints "missing
  gitleaks license" — so one transient API failure fails the job on precisely the kind of
  account that needs no key, and no input turns that off. The binary is also the same scanner
  the local history run used, so the evidence below and the CI gate are one tool. The
  `gitleaks` job verifies the release checksum before executing it, because the step downloads
  and runs a release asset; a version pin alone would not catch a tampered download.
  Rejected: the action, for the two reasons above, and a version pin without the checksum.
- D-T5.2 The scan range is `merge-base(base.sha, head.sha)..head.sha`, with neither
  `--first-parent` nor `--no-merges`, and the merge diff comes from D-T5.4's `--remerge-diff`.
  Each fact below was proven on a scratch branch rather than reasoned about (the evidence is in
  Progress):
  - *The merge base, not `base^..head`* (the shape gitleaks-action uses). On this branch
    `base^..head` covers 15 commits where the PR has 12: `base^` plus the commits main gained
    after the merge base, so a commit already on main is judged as part of the PR. A secret
    that reached main would then fail every unrelated PR opened against it — the E8 PR #182
    shape, judging a commit against something other than its own tree. `fetch-depth: 0` is
    what makes the merge base computable from a PR checkout.
  - *No `--first-parent`*, because this repository merges parallel lane branches into the epic
    branch (`feat/dev-tooling-t3` merges into `feat/dev-tooling`). It walks only the
    first-parent chain, so a lane arriving through a merge's second parent is never visited:
    proven as `0 commits scanned` and exit 0 for a PR whose lane carried a flagged string.
    Nothing is lost by dropping it, because the merge base — not `--first-parent` — is what
    excludes main: on this branch all 12 commits in `merge-base..HEAD` are feature commits and
    0 of them are ancestors of main.
  - *No `--no-merges`*, which came from gitleaks-action with the reasoning that a merge's diff
    only repeats the branch commits. That is false for a **conflict resolution**: the resolved
    lines exist in no other commit, so dropping the merge drops a credential nobody else
    carries. Two CRITICAL findings, one from the risk lens and one from the resilience lens,
    reported it independently, and it is proven as `0 leaks` over a branch whose only secret
    lived in a resolution. Note the other half of that trap: with no merge flag at all,
    `git log -p` emits **no diff** for a merge commit, so a merge is walked and read as empty.
    Reaching a resolution needs a flag that emits a merge diff, which is D-T5.4's job.
- D-T5.4 The merge diff is `--remerge-diff`, **not** `-m`, and `-m` was a regression the parent
  gate caught on 2cc0d0a. `-m` diffs a merge against **each** parent, so the diff against the
  feature parent carries all of main's content; this repository merges main into an epic branch
  before slicing chained PRs, so a clean merge of main re-reported main's own findings and the
  gate failed on content already on main — the E8 PR #182 shape again, one layer over.
  `--remerge-diff` diffs the merge against git's own automatic re-merge, so what shows is
  exactly what the resolution wrote and main is not re-reported. It needs git 2.36 or newer,
  which `ubuntu-latest` ships, and ci.yml names the floor because a runner that did not would
  fail the walk rather than skip it. Rejected: `-m` (re-reports main; also duplicates one
  finding per parent, so the same resolution logs twice where `--remerge-diff` logs once), and
  `--first-parent` on its own (D-T5.2's second bullet: it hides a whole lane).
- D-T5.5 `--remerge-diff` does **not** open the bypass the R1-001 finding of round
  `review-c0c0c311c59ce08f` claimed, and the flag stays. The claim was that credentials added
  manually during an **otherwise clean** merge commit escape the scan. They do not, and the
  reason is structural rather than incidental: the re-merge diff is *the merge result against
  git's own automatic re-merge of the two parents*, so any content unique to the merge commit
  necessarily differs from what the automatic merge would have produced, and therefore appears
  in the diff. Content can only fail to show if the automatic merge would have written the same
  thing, which means a parent already carried it and it is not new. Measured on a scratch
  worktree (removed afterwards), each case a real clean merge of main with the credential added
  by hand while merging, existing in no non-merge commit:
  - a **new file added during the clean merge** → `1 leak`, **exit 1**;
  - an **append to a file the feature side owns** → `1 leak`, **exit 1**, with
    `git log -1 --remerge-diff` printing the added line as an explicit `+` addition.
  For contrast on the same cases `--no-merges --first-parent` and `--cc` both report
  `no leaks found` (exit 0), and `-m` reports 4 — the shape that catches the manual edit but
  also re-reports main. The parent reproduced the clean-merge case independently and agreed
  R1-001 is false. This entry is the correction the round's single bounded budget was spent on:
  evidence, with no change to `ci.yml`.
- D-T5.3 gitleaks is CI-only and does **not** join `gate-fast`. `gate-fast` has no PR range,
  so the only shape it could use is a whole-history scan, and that fails on this repository
  today: 4 findings, all non-secrets, exit 1 (the evidence is in Progress). Hosting it would
  need a baseline file or an allowlist — a new artifact and new policy, to make a check pass —
  and `gate-lane` runs `gate-fast` on every commit of a lane, so it would put a 7.9 MB network
  download in the path of every commit checked, in the one recipe documented as needing no
  network. The cost is not time: cold download + verify + extract + scan measured 2.35 s. The
  feature doc's Scope only ever promised the CI step, and its acceptance criterion names CI.
  The four historical findings need no `.gitleaksignore` or baseline precisely because the
  range scan never walks them; a new one is a line-scoped `# gitleaks:allow`, never a blanket
  allowlist of the test tree, the same rule D-T2.6 states for ast-grep.

## Tasks
- [x] T1 `justfile`: `db-up`/`db-down`/`db-reset` per worktree, `gate-fast` (incl. single
  Alembic head), `gate *paths`, `gate-full`, `gate-lane base`. AGENTS.md Commands + the
  `D-Tx.n` convention in Workflow. Evidence: each recipe run, `gate-lane` over a range with
  one broken commit (in a scratch branch, deleted after).
- [x] T2 ast-grep: pinned dev dep, `sgconfig.yml`, rule `no-naive-today` + rule tests (RED
  first), wired into `gate-fast` and CI.
- [x] T3 pytest-randomly: dev dep; full suite on a clean DB with 3 seeds; flakes reported on
  #89 with seeds; fix only trivial ones.
- [ ] T4 **deferred to #194** by the owner (2026-09-29), out of scope for this feature.
  `alembic check` trial on a clean DB: adopt (with a small `include_object` filter if
  needed) into `gate-full` and CI, or reject with evidence in #139.
- [x] T5 gitleaks in CI (docs/09:78): verify the action/binary needs no license for this
  personal repo; run it locally once over the history.
- [ ] T5b CI cost: `concurrency` (group per workflow + PR ref, `cancel-in-progress: true`) on
  every PR workflow; per-JOB path filter (never workflow-level `paths:`, which leaves required
  checks pending): server job on `server/**`, `infra/**`, `.github/workflows/**`; web job on
  `web/**`, `.github/workflows/**`; ast-grep and gitleaks always. Skipped jobs must report
  success. Evidence: a web-only and a docs-only change skip the server job (act or a draft PR
  run, owner go needed for a push). Data: last 40 PRs, 24 server-only, 13 web-only; server
  job ~7 min, web ~2 min; repo private (2000 min/month free plan).
- [ ] T6 Close: `just gate-full` on a clean DB, CI ↔ justfile check list diff, feature doc
  progress, #139 comment with results. Plus, as its own `docs(agents)` commit (owner decision
  2026-09-29): trim AGENTS.md with the `writing-for-agents` skill from ~164 to ~100-110 lines —
  the ast-grep paragraph becomes one line stating WHEN to add a rule ("When a review catches the
  same mistake a second time, encode it as an ast-grep rule: `rules/<id>.yml` plus
  `rule-tests/<id>-test.yml` with valid and invalid cases"); the CI paragraph becomes one line
  (CI = `gate-fast` + the full suites + gitleaks, and gitleaks is CI-only); one line per just
  recipe; Layout keeps only the non-derivable facts; the review-findings bullets go 3 → 2. The
  docs table, Architecture rules and Testing sections stay.

## Acceptance criteria
- `just gate server/tests/logbook` runs static checks plus only those tests, against this
  worktree's own DB.
- `just gate-lane main` stops at a broken middle commit and names it; history unchanged.
- A naive `date.today()` in `server/src` fails `gate-fast` and CI with the rule's message.
- Two worktrees can run `just gate …` at the same time without touching each other's DB.
- CI runs ast-grep and gitleaks; nothing else in CI changes.

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
- T2 is done. Round 1, lineage `review-43815f8285e2faaf`, base `af07168`: candidate 10 files /
  249 lines, `high` risk (`shell_source` in `ci.yml`), four lenses (risk, resilience, readability,
  reliability), correction budget 125. No correction opened; approved and acknowledged,
  `authority: burned`. `R3-lane-node-modules`-class findings did not recur: risk reported none.
  Six non-blocking WARNINGs, all informational, filed as #195 and all fixed in the same session:
  `R2-astgrep-wrong-working-directory` (both commands now name `../sgconfig.yml` explicitly, in
  `gate-fast` and in CI), `R2-ci-gate-parity-ambiguous` (`ast-grep test` joined `gate-fast`, so
  CI's server job is exactly `gate-fast`'s seven server checks plus the full pytest run, and
  AGENTS.md names all seven instead of asserting parity), `R2-naive-time-comment-contradiction`
  (the comment now states the two naive shapes precisely, including the explicit `None`),
  `R3-blanket-timezone-ignore` (the `ignores` entry is **deleted**: `shared/dates.py` never needed
  an exemption, since its only read is `datetime.now(UTC)` and the naive value it handles arrives
  as an argument, and a file-level ignore shielded every naive read a future edit put there — the
  rule's `notes` now say that and point at a line-scoped `# ast-grep-ignore` instead), and
  `R3-no-naive-today-tz-none` / `R4-naive-now-tz-none` (the same gap from two lenses: four
  patterns added for `tz=None` and a positional `None`, under both import styles). The rule's
  remaining limit — an aliased `datetime` import is not matched — is written in the rule's own
  `notes` with the evidence that the repository imports no alias, so it lives where the next
  person editing the rule will read it rather than only in this doc.
- T3 is done, in its own lane (`feat/dev-tooling-t3` in the `dev-tooling-t3` worktree, a separate
  session) and merged into this branch at `822489a`. Round 1, lineage `review-7c760a6cbca01a44`,
  `high` risk (a `subprocess` call in the test), four lenses, **zero findings**; approved and
  acknowledged, `authority: burned`.
- T5 took two rounds and three parent-gated corrections, which is the record worth keeping: every
  one of them was a hole in the *git walk*, and none was visible by reading the command.
  - Round 1, lineage `review-095ab09de7d1a0a3`, base `780b46b`: 3 files / 200 lines, `high` risk
    (`shell_source` in `ci.yml`), four lenses, correction budget 100. Two CRITICALs from two
    lenses independently — `R1-001` (risk) and `R4-001` (resilience), both `deterministic`, both
    `introduced` — with one root cause: `--no-merges`, inherited from gitleaks-action, drops a
    merge commit, so a credential that exists only in a **conflict resolution** is never scanned.
    Fixed in the single bounded correction `2cc0d0a` (`-m`). Approved and acknowledged,
    `authority: burned`. Two non-blocking advisories survived as informational:
    `R2-PR-EVENT-SCOPE` (no explicit event guard, so a future `push` would pass empty base/head)
    and `R2-DUPLICATED-POLICY-RATIONALE` (the licence rationale duplicated in doc and YAML).
  - Round 2, lineage `review-c0c0c311c59ce08f`, base `2cc0d0a`: the correction only, 2 files / 90
    lines, `high` risk, budget 45. One CRITICAL, `R1-001` (risk, `deterministic`, `worsened`),
    claiming `--remerge-diff` opens a bypass for credentials added manually during an otherwise
    clean merge. **Refuted, not fixed**: the bounded correction `f660ab1` was spent on evidence
    in D-T5.5, with no change to `ci.yml`. The re-merge diff is the merge result against git's
    own automatic re-merge, so merge-unique content necessarily differs from it and shows up; both
    plausible shapes measure `1 leak` / exit 1. The parent reproduced the clean-merge case
    independently and agreed. Approved and acknowledged, `authority: burned`, with `R1-001`
    carried as a non-blocking advisory.
  - The parent gate rejected two of my own corrections before the round ever saw them: the
    `--first-parent` flag (a lane merged through a second parent was never visited, fixed in
    `dab375d`) and `-m` (it re-reports main when main is merged into an epic branch, replaced by
    `--remerge-diff` in `3596e06`). Both were proven on a scratch branch, both scratch
    worktrees removed afterwards.
  - `5b2993d` (`.gitleaksignore`, the two `65b273e` prose fingerprints) is part of the T5 slice
    and was parent-gated: the parent re-ran the exact scan over `merge-base(main, HEAD)..HEAD`
    and got no leaks, with `gate-fast` exit 0.

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
    ast-grep and gitleaks, and nothing else); T2 adds `alembic heads` to the
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
  - Second cycle, the parent's probe before the review: the rule reported `date.today()` but not
    `datetime.datetime.now()`, because a direct pattern cannot match a longer member chain. 14
    files in `server/src` use `import datetime`, so the gap was half the repository's style. RED
    after adding the three qualified invalid cases and the aware qualified valid ones:
    `FAIL no-naive-today  .............MMM` with `[Missing] Expect rule no-naive-today to report
    issues, but none found in: day = datetime.date.today()` and the same for
    `datetime.datetime.now()` and `datetime.datetime.utcnow()`; `0 passed; 1 failed` (exit 4).
    GREEN after the three extra patterns and a refreshed snapshot: `PASS no-naive-today
    ................` (16 cases), `1 passed; 0 failed`. The repo scan stayed clean, the probe now
    reports both shapes (`2 error(s) found`), and `datetime.datetime.now(UTC)` /
    `datetime.date(2026, 9, 29)` stay unreported.
  - Third cycle, the T2 review's six WARNINGs (#195), all fixed in one commit. RED after adding
    the four explicit-`None` invalid cases: `FAIL no-naive-today  ................MMMM`, with
    `[Missing] … none found in: now = datetime.now(tz=None)` and the same for `datetime.now(None)`,
    `datetime.datetime.now(tz=None)` and `datetime.datetime.now(None)`; `0 passed; 1 failed`
    (exit 4). GREEN after the four patterns and a refreshed snapshot: `PASS no-naive-today
    ....................` (20 cases), `1 passed; 0 failed`. The `ignores` entry is gone and the
    repo scan is still clean, which is the proof that `shared/dates.py` never needed it; the
    negative control now runs the other way, a `date.today()` appended to that file is
    **reported** (line 34, `1 error(s) found`) where T2's ignore hid it. `datetime.now(tz=None)`
    in a scratch module is reported, and `datetime.datetime.now(UTC)`, `datetime.now(tz=UTC)` and
    `datetime.date(2026, 9, 29)` stay unreported. Every scratch edit was reverted.

  - 2026-09-29 T5: a `gitleaks` job alongside `server` and `web` — no Postgres service, no
    uv/node setup — running the pinned binary 8.30.1 with the release checksum verified, over
    the commits the PR adds. `ci.yml` and `AGENTS.md` are the only two files touched, no new
    project dependency, and nothing else in CI changed.
  - **Licence, verified, not remembered.** gitleaks itself is MIT (`LICENSE`, 2019 Zachary
    Rice). `gitleaks-action` v2+ is not: "Since v2.0.0 of Gitleaks-Action, the license has
    changed from MIT to a license", © Gitleaks LLC. `GITLEAKS_LICENSE` is "required for
    organizations, not required for user accounts", and `gh repo view` reports this repo
    PRIVATE with a `User` owner, so the free path is real. D-T5.1 records why the binary was
    chosen anyway, the decisive part being the action's own source: `src/index.js` sets
    `shouldValidate = false` only inside the `type === "User"` branch of a
    `GET /users/{username}` call, and its `.catch` leaves it `true` and calls
    `process.exit(1)` with "missing gitleaks license".
  - **History scan, the STOP check — clean.** `gitleaks git -v --redact .` over HEAD:
    `499 commits scanned`, 5.16 MB, 426 ms, **4 findings**, exit 1. `499` is every non-merge
    commit in HEAD's ancestry, and it is the whole history for this purpose: the other 161 are
    merge commits, which gitleaks skips by default because a merge's diff is the branch commits
    it brings together, each already scanned on its own. All four findings are non-secrets,
    read by the `generic-api-key` rule:
    - `web/src/features/nodes/api/nodesApi.test.tsx:32`,
      `web/src/features/nodes/containers/CalibrationSheet.test.tsx:15` and
      `web/src/features/nodes/containers/NodeDetailSheet.test.tsx:30` — all the same
      assignment of the sensor metric name `soil_moisture_20cm` to a `channel_key` field,
      which the rule reads as a key because the identifier ends in `_key`. The literal is
      spelled out here in pieces on purpose: writing the assignment as one line, the way these
      three commits do, re-arms `generic-api-key` on this file, which is exactly what happened
      in 65b273e (see the `--first-parent` entry below and ci.yml's own comment);
    - `web/src/features/push/push.test.ts:7` — a VAPID **public** key, which is published to
      the push service by design and is not a secret.
    So: no real secret in history, no history rewrite, not a STOP. The sibling literals
    `token-abc`, `mqtt-secret` and `rotated-secret` in the same files are not reported
    (below the rule's entropy floor), which is the useful contrast — the four findings are
    the rule over-firing on a metric name, not a missed detection.
  - **Range green / red, both on real history, no scratch commits.** The `run` block was
    extracted from `ci.yml` with `yaml.safe_load` and executed as CI would run it, with
    `RUNNER_TEMP`, `GITLEAKS_VERSION`, `PR_BASE` and `PR_HEAD` set.
    - GREEN: `PR_BASE=main`, `PR_HEAD=HEAD` → checksum "La suma coincide", `11 commits
      scanned`, `no leaks found`, **exit 0**.
    - RED: a one-commit PR (`PR_BASE=e4dd324^`, `PR_HEAD=e4dd324`, the commit that added the
      two test fixtures) → `leaks found: 2`, **exit 1**. The build fails on a leak.
    - Tamper control: one byte appended to the downloaded archive → `La suma no coincide`,
      **exit 1** before `tar` runs, so the checksum is a real gate and not decoration.
  - **`--first-parent` was a real hole, found by the parent gate on 65b273e, and fixed.** The
    first cut of this job passed `--no-merges --first-parent`. This repository merges parallel
    lane branches into the epic branch, so on the final PR `--first-parent` walks only the
    first-parent chain and never visits a lane that arrived through a merge's second parent,
    while `--no-merges` drops the merge commit that would have reached it. Proof on a scratch
    worktree at `/tmp/opencode/t5-proof` (removed afterwards, with both scratch branches):
    `scratch/t5-epic` off `main`, a side branch `scratch/t5-lane` carrying one flagged string,
    merged `--no-ff` so the epic tip is a real 2-parent merge commit (first parent = main tip
    `9519e05`, second parent = the lane commit).
    - A) the shape that shipped in 65b273e, `--no-merges --first-parent`: `0 commits
      scanned`, `no leaks found`, **exit 0**. The lane's finding was invisible.
    - B) `--no-merges` only: `1 commits scanned`, `leaks found: 1`,
      `generic-api-key` in the lane's file, **exit 1**. The lane is scanned and the build fails.
    - The git-level reason, so the shape of the hole is unambiguous: the range holds 1
      reachable non-merge commit, and `--first-parent` reduces it to 0.
    - Nothing is lost by dropping it, because the merge base is what excludes main. On the real
      branch, `git rev-list --no-merges merge-base..HEAD` is exactly the 12 feature commits and
      0 of them are ancestors of main.
    - The payload is the same sensor-metric assignment the repository's history already trips
      `generic-api-key` on, so the proof's only variable is range reachability. Two earlier
      payloads were tried and **discarded because gitleaks did not report them**: the AWS
      documented example keys are allowlisted by the default config (`AKIAIOSFODNN7EXAMPLE` is
      not a finding), and a `DATABASE_PASSWORD` candidate that a loop over `gitleaks stdin` exit
      codes appeared to flag was, on isolated re-test, not flagged at all. A payload assumed to
      be detected is not evidence; this one is observed. The literal is named in pieces here
      for the reason given below.
  - **65b273e would have failed its own job, and the range fix is what exposed it.** Widening
    the range from `--first-parent` to the full merge-base range immediately reported 2 leaks on
    this branch, and both were in 65b273e: the feature doc and the job's own YAML comment had
    quoted the `channel_key` assignment verbatim in prose to document the false positive, so
    documenting it re-armed `generic-api-key` on those two files. Two facts worth keeping: a
    detector does not care that a secret-shaped string is inside a sentence, and a comment
    that explains a false positive can *become* one. Both lines now name the metric name and the
    field separately instead of pasting the assignment, and ci.yml says why. The line-scoped
    `# gitleaks:allow` was verified as the working alternative (honoured in a `.md` file too) and
    deliberately not used here, because the next person editing that prose would have to know to
    re-add it.
  - **`--no-merges` was a second, opposite hole, and the RDD round caught it.** After the
    `--first-parent` fix, the T5 RDD round (lineage `review-095ab09de7d1a0a3`, four lenses)
    returned two CRITICAL findings from two lenses independently — `R1-001` (risk) and
    `R4-001` (resilience), both `deterministic`, both `introduced` — with the same root cause
    from opposite directions: `--no-merges`, inherited from gitleaks-action, drops the merge
    commit, so a credential that exists only in a **conflict resolution** is never scanned. The
    action's own rationale for the flag is that a merge's diff merely repeats the branch
    commits, which is false for a resolution. Proof on a scratch worktree
    (`/tmp/opencode/t5-proof2`, removed afterwards): two branches edit the same line, the merge
    conflicts, and the resolution writes a flagged value, so the string exists in **no**
    non-merge commit of that branch — `git log -S … --no-merges HEAD` lists none of the
    scratch commits.
    - `--no-merges` (what shipped): `3 commits scanned`, `no leaks found`, **exit 0**. Missed.
    - no merge flag at all (the fix that looks right): `3 commits scanned`, `no leaks found`,
      **exit 0**. Still missed — `git log -p` emits no diff for a merge commit at all, so
      removing the flag walks the merge and reads nothing. This is the trap: the obvious
      correction is not a correction.
    - `-m`: `4 commits scanned`, `2 leaks found`, **exit 1**. The resolution is read, but this
      flag was itself the next regression — see the D-T5.4 entry below. The final flag is
      `--remerge-diff`, which reports the same resolution as **1** leak, once, because there is
      one diff instead of one per parent.
    The lesson is the same one the `--first-parent` fix carries: neither hole was visible by
    reading the command, and both were visible in one scratch run. Reasoning about a git walk
    is not evidence; a merge on a scratch branch is.
  - **`-m` was a third hole, caught by the parent gate on 2cc0d0a.** `-m` was the fix for the
    conflict-resolution hole and it broke the other half of the problem. It diffs a merge
    against **each** parent, so the diff against the feature parent carries all of main's
    content, and this repository merges main into an epic branch before slicing chained PRs.
    The parent proved it and I reproduced it before changing anything: branch from `e4dd324^`,
    two feature commits, then `e4dd324` (the commit that added the fixtures) merged in as
    "main"; over `merge-base(e4dd324, HEAD)..HEAD`, `-m` reported `2 leaks` in main's own
    `CalibrationSheet.test.tsx` and `NodeDetailSheet.test.tsx` and exited 1 — the gate failing
    on content already on main, the E8 PR #182 shape one layer over.
  - **The final flag is `--remerge-diff`**, which diffs the merge against git's own automatic
    re-merge, so only what the resolution wrote shows. All four cases on one scratch worktree
    (`/tmp/opencode/t5-proof3`, removed afterwards with every scratch branch):
    | case | required | `--remerge-diff` |
    |---|---|---|
    | clean merge of main | no false positive | `no leaks found`, exit 0 |
    | conflict resolution writing a flagged value | caught | `1 leak`, exit 1 |
    | second-parent lane carrying a secret | caught | `1 leak`, exit 1 |
    | `--no-merges --first-parent` | shows both original holes | `no leaks found`, exit 0 |
    On the conflict case the three flags read `--no-merges` 0 leaks, `-m` 2 leaks,
    `--remerge-diff` 1 leak. It needs git 2.36 or newer, and ci.yml names that floor.
  - `actionlint` 1.7.12 on `ci.yml`: exit 0, no findings. Its `shellcheck` rule is **skipped
    silently** when shellcheck is not on `PATH` (first run reported `Rule "shellcheck" was
    disabled`), so shellcheck 0.11.0 was installed outside the repo and the run repeated:
    `actionlint -verbose` no longer reports the rule disabled, and the extracted script is
    clean at `--severity=style -s bash` (exit 0). Positive control on a scratch workflow (a
    missing `fi`) is reported, so the rule is live rather than merely enabled.
  - `just gate-fast` → exit 0, all nine static steps (seven server, `eslint`, `tsc`). Nothing
    in this task touches the recipes; the run confirms the tree.
  - Not run here, by design: a live Actions run. The brief forbids a push without the owner's
    go, so the job's first real execution is the parent's gate on the PR.
  - T5 closed: the parent gate passed on `5b2993d` (their own run: `gitleaks --remerge-diff` over
    `merge-base(main, HEAD)..HEAD` → no leaks, `gate-fast` exit 0), and the T3 lane was merged in
    as `822489a` (clean, no conflicts). After that merge, on the merged branch: `uv lock --check`
    OK, `gate-fast` exit 0, `gitleaks` over 19 commits **including the real lane merge** → no
    leaks, and `just gate server/tests/simulator` → 34 passed. That last run is the one that
    matters for T5b: the lane merge is a real two-parent merge in this repository's history, and
    the committed scan shape walks it and stays green.
- 2026-09-29 T3: `pytest-randomly` in the server dev group, in its own lane and session
  (`feat/dev-tooling-t3` in the `dev-tooling-t3` worktree), merged into this branch at `822489a`.
  Two commits: `b79b5a0` `fix(test)` and `5e9b374` `build`.
  - `b79b5a0` `fix(test)`: a baseline-scoped assertion in `server/tests/simulator/test_provision.py`.
    RED first — the assertion failed under `--randomly-seed=101` before the fix.
  - `5e9b374` `build`: `pytest-randomly>=5.0.0` as the dev dependency, plus the AGENTS.md
    Testing note.
  - Three full seeded runs on a clean database: seeds **101, 202 and 7**, `1120 passed` each, no
    failures on any of them.
  - Four failures appeared only on an **abandoned** database, and did not reproduce on a clean
    one. They are filed as unreproduced in #89
    (https://github.com/jab16jy/techcamp-v2/issues/89#issuecomment-5899715148), together with the
    real gap they pointed at: a test that writes without `db_session` gets no teardown. That gap
    is #89's own scope and is not fixed here — the finding is that the seeded runs are clean, so
    this feature does not own it.
  - D-T3.1: a seed is a **session knob**, not a test annotation. `-p no:randomly` also drops the
    per-test `random.seed()` reset, while `--randomly-dont-reorganize` keeps that reset and only
    pins the file order, so the latter is the one to reach for while debugging a single test.
  - RDD lineage `review-7c760a6cbca01a44`, `high` risk (a `subprocess` call in the test), four
    lenses, **zero findings**; approved and acknowledged, `authority: burned`.

## Next step
T1, T2, T3 and T5 are done and merged into this branch (T3 as `822489a`), with their review rounds
closed, approved and acknowledged. T4 is deferred to #194 by the owner and is out of this
feature's scope. What is left is **T5b, in a fresh session**, then T6.

T5b (`concurrency` plus per-JOB path filters) inherits three things from T5, all of them learned
the hard way:
- the `gitleaks` job must stay **always-on**. It reads `github.event.pull_request.base.sha` and
  `head.sha` and `fetch-depth: 0`, so a path filter that skips it on a docs-only change is safe
  for the scan but breaks the required check.
- a skipped job must **report success** for required checks to pass, which is why the filter goes
  at job level with an explicit `if:` and never at workflow level with `paths:`.
- the lane merge at `822489a` is now a real two-parent merge in this history, so any T5b change to
  the scan walk has to keep it green — the flag is `--remerge-diff` over
  `merge-base(base, head)..head`, and `5b2993d`'s `.gitleaksignore` must not become a blanket
  allowlist.

T6 then closes the feature: `just gate-full` on a clean DB, the CI ↔ justfile check-list diff,
and the AGENTS.md trim that is now on its checklist as its own `docs(agents)` commit.
