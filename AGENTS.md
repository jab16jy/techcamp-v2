# AGENTS.md

Instructions for every coding agent in this repository (Claude Code, OpenCode, Codex, Gemini).
`CLAUDE.md` imports this file; edit here, not there.

TechCamp v2: field IoT and irrigation decision support for smallholder farms in the Colombian
Caribbean.

## Docs are the primary source

`docs/` is the source of truth for product and design. Code follows the docs.

1. Before designing or changing behavior, read the doc that owns it (map below) and the ADRs it
   cites. Cite the doc section or ADR in your plan, commit or PR.
2. When code and docs disagree, the docs win: fix the code, or stop and ask the owner.
3. A change that needs a different design updates the doc or adds an ADR first (use the
   `domain-modeling` skill), in the same work unit as the code.
4. Use the domain terms from [docs/00-glosario.md](docs/00-glosario.md) in identifiers and prose.

| Topic | Owner doc |
| --- | --- |
| Index and reading order | [docs/README.md](docs/README.md) |
| Glossary | [docs/00-glosario.md](docs/00-glosario.md) |
| Requirements, estimates | [docs/01-requisitos.md](docs/01-requisitos.md), [docs/02-estimaciones.md](docs/02-estimaciones.md) |
| Data model, API | [docs/03-modelo-datos.md](docs/03-modelo-datos.md), [docs/04-api.md](docs/04-api.md) |
| Architecture, detailed design | [docs/05-arquitectura.md](docs/05-arquitectura.md), [docs/06-diseno-detallado.md](docs/06-diseno-detallado.md) |
| Frontend design system | [docs/07-frontend-design-system.md](docs/07-frontend-design-system.md) |
| ML | [docs/08-ml.md](docs/08-ml.md) |
| Security, org isolation, bottlenecks | [docs/09-cuellos-de-botella.md](docs/09-cuellos-de-botella.md) |
| Build order (epics E0…E16) | [docs/10-dag.md](docs/10-dag.md) |
| Metrics | [docs/11-metricas.md](docs/11-metricas.md) |
| Decisions | [docs/adr/](docs/adr/) |

For library and framework APIs, current docs come from the `find-docs` skill (ctx7), not memory.

## Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg, Alembic, procrastinate (jobs in
Postgres, [ADR-0012](docs/adr/0012-jobs-en-postgres.md)); one PostgreSQL with PostGIS,
TimescaleDB and pgvector ([ADR-0003](docs/adr/0003-postgres-unico.md)); Mosquitto MQTT; React 19
+ Vite + strict TypeScript + Tailwind v4. Modular monolith, hexagonal per module
([ADR-0002](docs/adr/0002-monolito-modular.md)). The seminar profile runs locally with emulators
([ADR-0021](docs/adr/0021-perfil-seminario-local.md)); `TECHCAMP_PROFILE=production` switches it.

## Layout

- `server/src/techcamp/<module>/{domain,application,adapters}` for `identity`, `farms`,
  `telemetry`, `weather`, `irrigation`, `alerts`, `notifications`, `logbook`, `risk`, `metrics`,
  `assistant`; cross-cutting code in `shared/`; app entry `main.py`.
- `server/migrations/` Alembic; `server/tests/` mirrors modules.
- `web/src/design-system/` tokens and primitives, `web/src/app/` screens.
- `infra/` Compose and Mosquitto; `ml/` offline training with its own deps; `odd/tasks/` feature
  docs.

## Commands

Server (run in `server/`). Tests hit a real Postgres: start the stack first; `DATABASE_URL`
defaults to `postgresql+asyncpg://techcamp:techcamp@localhost:5432/techcamp`, and the session
fixture migrates to head and downgrades after.

- `uv run pytest` · one test: `uv run pytest tests/identity/test_x.py::test_name`
- `uv run ruff check` · `uv run ruff format` · `uv run mypy` · `uv run lint-imports`

Web (run in `web/`):

- `npm run dev` · `npm run lint` · `npm run typecheck` · `npm run build` · `npm run size`
- `npm test` is Vitest watch mode; one run: `npm test -- --run`; one file:
  `npm test -- --run src/App.test.tsx`

Infra: `podman-compose -f infra/compose.yaml --profile seminar up` (Docker: `docker compose`).

CI (`.github/workflows/ci.yml`) runs all of the above, `ruff format --check` and the size budget
included; green locally means green in CI.

## Architecture rules

- Hexagonal layers per module: `domain` has no I/O and imports nothing else; `application`
  depends only on `domain`; `adapters` depend on `application`. `import-linter` enforces it.
- A port exists only with two real implementations, or when external I/O needs a test double.
- Every repository filters by `org_id` ([docs/09](docs/09-cuellos-de-botella.md#seguridad)).
- English identifiers, comments and code; Spanish UI copy only where docs/07 requires it.
- UI work goes through the `impeccable` skill, following docs/07 and
  [ADR-0006](docs/adr/0006-design-system.md). Tokens, primitives and screens live only there.
- ML ([ADR-0020](docs/adr/0020-protocolo-de-experimentacion-ml.md)): agents leave the harness,
  the test set and the promotion gate untouched.

## Testing

TDD is on from E2 (owner decision 2026-09-22): observe RED, then GREEN, then REFACTOR for every
behavior change. Pytest for domain and application; domain tests are pure. Test doubles only at
ports for external I/O (LLM, Open-Meteo). Org isolation tests per docs/09. FAO-56 numeric examples
cover irrigation math. Vitest for web units; Playwright for e2e and scenarios arrives with E16.

## Workflow

- Organic Driven Development: one feature doc per epic at `odd/tasks/<feature>.md`, in the build
  order of docs/10. Branch per epic.
- Conventional Commits, no AI attribution. Work-unit commits, receipt-driven review per commit.
- Delivery: stacked-to-main chained PRs of about 400 authored lines, merged in order.
- Skills: the Agent Teams Lite registry `.atl/skill-registry.md` (local, gitignored; rebuild with
  `gentle-ai skill-registry refresh`) is the skill index. Delegators pick matching skills there
  and pass their exact `SKILL.md` paths to subagents.
- Project-mandated skills: `impeccable` (UI), `domain-modeling` (ADR, glossary), `tdd`,
  `work-unit-commits` and `chained-pr` (delivery), `find-docs` (library docs),
  `systematic-debugging` (bugs), `playwright-cli` (e2e).
