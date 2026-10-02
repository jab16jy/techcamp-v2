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
- [ ] T1 #141 residual: reject plot+node both in get_escalation_target (RED first), close the issue with the verified status of the other four findings. Surfaces: server/src/techcamp/alerts/adapters/repositories.py, server/tests/alerts/test_escalation.py.
- [ ] T2 #89: reproduce and fix the teardown deadlock and the procrastinate order-flake. Surfaces: server/tests/conftest.py, server/tests/telemetry/test_jobs.py, and what the explorer finds.
- [ ] T3 #234 just gate-release: merge origin/main check, gate-lane in parallel with gate-full and two more seeds, print every seed. Surfaces: justfile, AGENTS.md (Commands).
- [ ] T4 #234 PR validation workflow: Conventional Commit title, epic and type labels, Refs/Closes #N (Dependabot exempt). Surfaces: .github/workflows/.
