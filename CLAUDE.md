# TechCamp v2

Field IoT + irrigation decision support for smallholder farms in the Colombian Caribbean.
Full design: [docs/README.md](docs/README.md). Decisions: `docs/adr/`.

## Stack

Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg, Alembic, procrastinate
(jobs in Postgres, [ADR-0012](docs/adr/0012-jobs-en-postgres.md)); PostgreSQL with PostGIS,
TimescaleDB, pgvector ([ADR-0003](docs/adr/0003-postgres-unico.md)); Mosquitto MQTT; React 19 +
Vite + strict TypeScript. Monolith modular, hexagonal per module
([ADR-0002](docs/adr/0002-monolito-modular.md)). Seminar profile runs local with emulators
([ADR-0021](docs/adr/0021-perfil-seminario-local.md)).

## Repo layout

`server/src/techcamp/{identity,farms,telemetry,weather,irrigation,alerts,notifications,
logbook,risk,metrics,assistant,shared}` (each: `domain/`, `application/`, `adapters/`) ·
`web/` (PWA) · `infra/` (Compose, Mosquitto) · `ml/` (offline training, separate deps) ·
`odd/` (ODD feature docs).

## Commands

- Server: `uv run pytest`, `uv run ruff check`, `uv run ruff format`, `uv run mypy`,
  `uv run lint-imports`.
- Web: `npm run dev`, `npm run lint`, `npm run typecheck`, `npm test`. Playwright arrives with
  E16 (scenario e2e).
- Infra: `podman-compose -f infra/compose.yaml --profile seminar up`.

## Testing policy

Pytest for domain/application; domain tests are pure, no I/O. Test doubles only at ports for
external I/O (LLM, Open-Meteo). Org isolation tests per [docs/09](docs/09-cuellos-de-botella.md).
FAO-56 numeric examples cover irrigation math. Vitest for web units, Playwright for e2e/scenarios
(E16). TDD: on from E2 (owner decision 2026-09-22) — RED → GREEN → REFACTOR for every behavior
change; observe the failing test before implementing. Runners: `uv run pytest` (server), `npm
test` (Vitest, web).

## Architecture rules

Hexagonal layers per module: `domain` has no I/O and imports nothing else; `application` depends
only on `domain`; `adapters` depend on `application`. Enforced by `import-linter`
([ADR-0002](docs/adr/0002-monolito-modular.md)). A port exists only with two real implementations
or when external I/O needs a test double. Every repository filters by `org_id`
([docs/09](docs/09-cuellos-de-botella.md#seguridad)). English identifiers, comments and code;
Spanish UI copy only where [docs/07](docs/07-frontend-design-system.md) requires it.

## Frontend rule

All UI work goes through the `impeccable` skill, following
[docs/07-frontend-design-system.md](docs/07-frontend-design-system.md) and
[ADR-0006](docs/adr/0006-design-system.md). No tokens, primitives or screens outside it.

## Skills

`impeccable` (frontend/design), `ponytail` (minimal implementation), `find-docs` (current library
docs via ctx7), `work-unit-commits`, `chained-pr`, `playwright-cli` (e2e), `systematic-debugging`,
`domain-modeling` (ADR/glossary changes).

## Workflow

Organic Driven Development: one feature doc per epic at `odd/tasks/<feature>.md`, following the
build order in [docs/10-dag.md](docs/10-dag.md). Branch per epic. Conventional Commits, no AI
attribution. Receipt-driven review per work-unit commit. Delegated work runs on sonnet subagents.

## ML rule

Per [ADR-0020](docs/adr/0020-protocolo-de-experimentacion-ml.md): agents never modify the
harness, the test set or the promotion gate.
