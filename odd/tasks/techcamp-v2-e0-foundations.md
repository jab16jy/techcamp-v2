# TechCamp v2 — E0 Foundations

## Objective
Deliver epic E0 from `docs/10-dag.md`: `docker compose --profile seminar up` starts Postgres (PostGIS, TimescaleDB, pgvector), Mosquitto, MinIO, and empty api and web services; CI is green with lint, types, tests and import-linter. Add a root `CLAUDE.md` (50–80 lines) that fixes the tooling, skills and workflow for every later epic.

## Why
E0 is the root of the critical path; E1 (design system, Impeccable) and E2 (identity) both depend on it.

## Scope
- In: `CLAUDE.md`, `infra/` (Compose + Mosquitto config), `.env.example`, `server/` skeleton (the 12 module packages, hexagonal import-linter contracts, `/health`), `web/` technical skeleton (no visual design), GitHub Actions CI.
- Out: any UI design, tokens or components (E1, Impeccable owns them), auth (E2), `ml/`, `firmware/`, Caddy and the `ops` profile (E14).

## Constraints
- Follow `docs/05-arquitectura.md` (stack, repo layout, hexagonal layers, seminar profile) and ADR-0002/0003/0012/0017/0018/0021.
- Ponytail: no speculative scaffolding; empty module packages only, no ports without two implementations.
- Pin exact versions from current docs (ctx7), not memory.
- English code, identifiers and comments; neutral professional wording.
- Local runtime is Podman (no Docker, no compose provider installed); the Compose file stays standard.

## Route and checks
- TDD: on from E2 (owner, 2026-09-22; source: owner decision in chat, recorded in CLAUDE.md). E0 has no domain logic: smoke tests only.
- Runners: `uv run pytest`, `uv run ruff check`, `uv run mypy`, `uv run lint-imports` (server); `npm run lint`, `npm run typecheck`, `npm test` (Vitest) (web).
- Route: delegated direct, one sonnet writer. Triggers fired: writer (10+ non-trivial files), preparation (reads 5+ design docs).
- Delivery: `single-pr` (owner: architecture PR, large size accepted). Forecast ~800 authored lines. Branch `feat/e0-foundations`.
- RDD: on (global). `gentle-ai review assess --committed-only` per work-unit commit; first boundary is the branch point `94cec05`.

## Tasks
- [x] T1 `CLAUDE.md`: stack, commands, test/lint policy, skills per area, ODD/RDD workflow, design-system rules — route: delegated
- [ ] T2 `infra/` Compose seminar profile + `.env.example` + Mosquitto config — route: delegated
- [ ] T3 `server/` skeleton: uv project, 12 module packages, import-linter contracts, `/health`, smoke test, ruff/mypy — route: delegated
- [ ] T4 `web/` skeleton: Vite + React 19 + strict TS, ESLint, Vitest smoke test — route: delegated
- [ ] T5 CI workflow: server and web jobs — route: delegated

## Acceptance criteria
- All server and web checks above pass locally.
- Compose config validates; services start under Podman or the gap is reported.
- import-linter forbids `domain → application/adapters` and `application → adapters` in every module.
- `CLAUDE.md` is 50–80 lines and consistent with the design docs.

## Progress / evidence
- Branch created from `main` @ `94cec05`.
- T1 done: `CLAUDE.md` (69 lines, within 50-80) + `.gitignore`. `wc -l CLAUDE.md` → 69. Commit: pending (staged with this doc update).

## Next step
T2.
