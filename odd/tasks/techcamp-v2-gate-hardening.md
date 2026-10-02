# TechCamp v2 — Gate Hardening and Flake Follow-ups

## Objective
Make the pre-PR gate fast and trustworthy before E10/E11 start, and close the one open production-adjacent follow-up. Issues: #234, #89, #141 (#192 stays in E10).

## Problem
- E9 lost about two hours between the last local gate and main (#234): one gate-full seed, order-dependent tests, stacked PRs based on the previous slice.
- #89: Timescale background jobs deadlock the Alembic downgrade in session teardown; a procrastinate order-flake in tests/telemetry/test_jobs.py is unreproduced.
- #141: a review of the T8 escalation slice left findings; verified 2026-10-01 on main, R3-ineligible-continue-livelock and R3-unresolvable-target-poison-pill were fixed by c8e3e0f (D42), R3-node-branch-untested by test_escalation.py:374, R3-no-concurrency-proof by :696. Only R3-both-targets-accepted is still open (get_escalation_target takes plot_id and node_id both without raising).

## Scope
- In: #234 gate-release recipe (steps 1-4 of the rule in the issue) and PR validation workflow; #89 diagnosis and fix; #141 residual.
- Out: #192 (E10, ml/ does not exist yet), the E5-E9 review-follow-up backlog, #146 (owner-only manual proof), the optional CodeRabbit base_branches change.
- Out until the owner decides: marking the PR validation check as required in protect-main (repo setting, not a file).

## Constraints
- AGENTS.md: docs win; English artifacts; Conventional Commits, no AI attribution; org_id on every repository.
- TDD for behavior changes (#141, #89 if the fix is code). Recipes and workflows get a functional check instead.
- Writers are single-threaded; read-only explorers run in parallel.

## Route and checks
- Branch chore/gate-hardening. Parent orchestrates; read-only explorers map T2 and T3/T4 first; one gentle-ai-worker per task, one commit per task. gentle-ai-verify runs commands.
- Fast checks per commit: just gate-fast plus the targeted tests. Full gate once at the end.

## Review (RDD)
- Candidate is each work-unit commit; native review runs only under the user-owned RDD switch.

## Tasks
- [x] T1 #141 residual: reject plot+node both in get_escalation_target (RED first), close the issue with the verified status of the other four findings. Surfaces: server/src/techcamp/alerts/adapters/repositories.py, server/tests/alerts/test_escalation.py.
- [x] T2 #89: reproduce and fix the teardown deadlock and the procrastinate order-flake. Surfaces: server/tests/conftest.py, server/tests/telemetry/test_jobs.py, and what the explorer finds.
- [x] T3 #234 just gate-release: merge origin/main check, gate-lane in parallel with gate-full and two more seeds, print every seed. Surfaces: justfile, AGENTS.md (Commands).
- [x] T4 #234 PR validation workflow: Conventional Commit title, epic and type labels, Refs/Closes #N (Dependabot exempt). Surfaces: .github/workflows/.

## Evidence
- T1 `8c25fe0`: RED `Failed: DID NOT RAISE ValueError` (src reverted), GREEN, `tests/alerts` 159 passed; ruff, format, mypy, lint-imports green. The other four #141 findings were already closed on main (c8e3e0f, `test_escalation.py` node and concurrency tests) and are noted on the issue.
- T2 `09177db`: no RED (test infrastructure, mechanism inferred, not observed). The session fixture re-armed the Timescale jobs right before the downgrade; `alter_job` keeps `next_start`, so a policy that fell due fired at once and raced the DROPs. The restore is gone; the lock test clears foreign telemetry-queue jobs before its queue-wide fetch. Verified by gentle-ai-verify: `tests/telemetry` 225 passed under seeds 1, 2, 3 with a clean teardown, plus an alerts+telemetry run under seed 7. Not reproduced before the fix, so #89 stays open until `gate-release` has run clean a few times.
- T3 `1d6eaf6`: `gate-release base="origin/main" extra_seeds="2"`; both fail-fast paths and the rc/summary/trap skeleton exercised by the worker with stubs. Positional arguments only (`set positional-arguments`).
- T4 `f6eb289`: replayed against the last 99 non-Dependabot merged PRs, 0 title failures; negative titles all fail.

## Decisions
- D-T4.1 (owner, 2026-10-01): the Conventional Commit title fails the check; missing epic:*, type:* and Refs/Closes #N only warn. Measured on the last 100 merged PRs: 75 lacked type:*, 26 epic:*, 47 a reference, so a hard rule would be red on most of them.
- D-T4.2: the workflow is separate from ci.yml (no suite re-run on a label edit). It cannot be a required check on the free plan (D-T5b.1) and GitHub orders no workflows against each other; it takes seconds, so it ends before CI's suites.
- D-T4.3: the title scope allows commas, `fix(irrigation,farms,telemetry)` is in use on main.
