# TechCamp v2 — E12 Agronomic assistant (RAG + LLM with cited sources, limits and degradation)

## Objective

Deliver epic **E12 "Asistente"** from `docs/10-dag.md:75` — *"Asistente con fuentes citadas, límites y
degradación"*, deps E6, E7, E10 — as specified in `docs/06-diseno-detallado.md:344-387` (§9 Asistente
agronómico), `docs/04-api.md:227-228` (the two endpoints), `docs/03-modelo-datos.md:347-376`
(`kb_document`, `kb_chunk`, `conversation`, `message`) and `docs/07-frontend-design-system.md:70,129`
(the reserved screen path and its place under **Más**). Requirement RF-14 (`docs/01-requisitos.md:37`).

Feature identity: `techcamp-v2-e12-assistant`. This file is E12's only feature doc. It does **not**
replace `techcamp-v2-e12-blockers.md` (the pre-implementation assessment, kept as the B/C evidence
this plan cites) nor any other epic's doc.

## Problem

Nothing exists. `server/src/techcamp/assistant/` holds four empty `__init__.py` files; there is no
`server/tests/assistant/`; no migration creates `vector`, `kb_document`, `kb_chunk`, `conversation` or
`message` (only `postgis` at `9098dc0927a3:26` and `timescaledb` at `8c3983dc2dfd:79` do that);
`pgvector` is not a `server/pyproject.toml` dependency; `grep 429|Retry-After` over `server/src`
returns only the Open-Meteo client; no `llm_cost_cop_total` metric has a home
(`docs/09-cuellos-de-botella.md:86`); `web/src/features/assistant/` does not exist and
`web/src/lib/api/schema.d.ts` has no assistant path.

Four facts about the served risk make the epic's headline claim harder than it looks, and they shape
T4: the served `model_version` is the `climatology_month` baseline carrying `thresholds={}`, so **every
served prediction is `low`** (D-T9.2 in `techcamp-v2-e10-climate-risk.md`); `drought` has no registered
predictor (D-T9.3, only `risk_flood`); and `risk` repositories carry no `org_id` by design
(`risk/adapters/repositories.py:3-8`, `docs/03-modelo-datos.md:415`).

## Why

RF-14 is the channel ADR-0007 exists for: a low-literacy producer cannot read a dashboard, but can ask
*"¿por qué no regué yesterday?"*. ADR-0007:26 records that v1's LangGraph graph was linear
(START → orchestrator → generate → END) with no cycles, no tool loop and no checkpoints, so v2's flow is
deterministic: gather facts → retrieve fragments → **one** LLM call. E12 also closes the pending owner
decision *"Proveedor de LLM y de embeddings (evaluación con 30 preguntas)"* scheduled for E12
(`docs/README.md:68`), and it feeds E15, the seminar demo (`docs/10-dag.md:52`).

## Scope

**In**

- `assistant` module, hexagonal: `domain` (prompt policy, citation shape, limits as pure rules),
  `application` (use cases + ports + DTOs), `adapters` (SQLAlchemy repositories, HTTP router, provider
  and embedding clients, ingestion job).
- Ports `LLMClient` (`stream(messages) → tokens`) and `Embedder` (ADR-0007:14) plus
  `KnowledgeRepository`, conversation/message persistence and a usage/quota port, each with test
  doubles (ports are justified by external I/O needing a double, `docs/05-arquitectura.md:175`).
- Schema: `CREATE EXTENSION vector`, `kb_document`, `kb_chunk` + HNSW cosine
  (`docs/03-modelo-datos.md:558`), `conversation`, `message` with the cost and time columns T6 needs.
- Corpus ingestion: chunking ~500 tokens with overlap, source + licence per document
  (ADR-0008:12,32), ENSO state on `kb_document.enso_state` (docs/06:374), a procrastinate job
  (ADR-0012) with a `/dev` trigger (ADR-0021).
- Fact builders for sensors, alerts, irrigation, ENSO/seasonal climate and risk, assembled
  deterministically **before** the single LLM call (docs/06:369-375), and the no-LLM degraded answer
  that returns those same facts plus the relevant documents (docs/06:377).
- `POST /assistant/messages` (`text/event-stream`: tokens + cited sources) and
  `POST /assistant/messages/{message_id}:feedback` (`docs/04-api.md:227-228`).
- The 10-questions/day limit with `429` + `Retry-After` (`docs/04-api.md:22`), the budget cap with
  degradation, and the cost ledger the budget metric reads (`docs/06:365`, `docs/09:86`,
  `docs/11-metricas.md:132`).
- `web/src/features/assistant/` — the screen docs/07 reserves, wired under **Más**, design-system
  tokens and primitives only, built through the `impeccable` skill.
- The 30-question Caribbean eval and the provider ADR (ADR-0007:15).

**Out**

- Hybrid BM25 + vector search. Only if the eval shows failures on exact terms, and then as its own
  decision (ADR-0008:19).
- The E10 risk screen (#277, open). E12 cites the risk prediction; it does not render a risk tab.
- M3 / drought. `drought` has no registered predictor (D-T9.3); the risk fact degrades to "not
  available" instead of inventing a value.
- The API-wide 120 requests/minute limiter (`docs/04-api.md:22`). It is not the assistant's control and
  belongs with the `/metrics` and operations work (E14). T6 owns the assistant's `429` contract and
  files the rest as a follow-up rather than silently absorbing it.
- `DELETE /me` / `GET /me/export` payload changes for conversations (Ley 1581). E12 defines what
  `conversation`/`message` hold and flags the deletion question (Open question 3); it does not build
  the erasure job.
- A second LLM provider as a runtime choice. The port is provider-agnostic (ADR-0007:14); exactly one
  adapter ships.

## Constraints

- **Docs are the source of truth** (AGENTS.md rule 1). T0 writes every decision below into its owner
  doc *before* the code that depends on it, in the same work unit. Every task cites the doc section or
  ADR it implements.
- **Hexagonal** (ADR-0002): `domain` has no I/O and imports nothing; `application` depends only on
  `domain`; `adapters` depend on `application`. `techcamp.assistant` is already whitelisted in the
  import-linter containers (`server/pyproject.toml:109`) — no contract change needed, the existing one
  already covers the module.
- **Cross-module reads go through the public `application` facade, never `domain`, never another
  module's tables** (`docs/05-arquitectura.md:150-151`). `assistant` calls `farms`, `telemetry`,
  `irrigation`, `alerts` and `risk` facades (docs/05 graph edges `assistant --> farms|telemetry|
  irrigation|alerts|risk`, lines 125-129). `resolve_plot_access`
  (`farms/application/manage_plots.py:29`) is the access gate, exactly as in `home` and `risk`.
- **`org_id` + `404`** (AGENTS.md:91, `docs/09-cuellos-de-botella.md:71`,
  `docs/04-api.md:16`): every repository filters by org or by owning user, and a resource of another
  org answers `404`, never `403` and never an empty `200`.
- **One LLM call, no agents, no Ollama** (ADR-0007:13-17,26). Deterministic flow; no agent framework
  and no provider tool-use. **The LLM explains; it never computes** irrigation, risk or doses
  (ADR-0007:16).
- **Prompt rules are fixed by docs/06:381-387**: plain Spanish; only the delivered plot facts and
  fragments, with the source cited; say so when evidence is insufficient; **never invent agrochemical
  doses** — point to the product label registered with ICA and to the technician.
- **Prompt-injection controls** (docs/09:75): RAG chunks and the user's text travel as *data*, the
  assistant has no writing tools, output is text only. **Minimum data to the provider, no personal data**
  (ADR-0007:39), disclosed in the versioned consent.
- **Spanish is the UI language only where docs/07 requires it.** Identifiers, comments, docstrings and
  this document are English (AGENTS.md §Architecture rules); the glossary's terms
  (`docs/00-glosario.md`) are the vocabulary the assistant's answers must use.
- **UI through `impeccable`**, following docs/07 and ADR-0006. Tokens, primitives and screens live only
  in `web/src/design-system/`.
- **TDD RED → GREEN → REFACTOR for every behavior change** (AGENTS.md:100). Org isolation tests per
  docs/09. Test doubles only at ports for external I/O.
- Library APIs verified with `find-docs` (ctx7) before use; no invented API surface.

## Decisions (T0) — `D-T12.1` … `D-T12.6`

Each one is cheap now and expensive after the schema or the SSE endpoint exists. All six are written
into their owner doc by T0, in the same work unit as the doc change.

- **D-T12.1 — Cost and time live on `message`; the budget is a `SUM`, not a ledger table.**
  `message` as specified (`docs/03-modelo-datos.md:368-376`) stores tokens and `helpful` and **no
  timestamp and no cost**, yet three things need both: the daily limit (`docs/04-api.md:22`), the
  monthly budget (`docs/11-metricas.md:132`, `gasto del mes / tope`) and docs/06:365 (*"guardar mensaje,
  tokens y costo"*). T2 adds `created_at timestamptz NOT NULL`, `model text NULL`,
  `cost_cop numeric(12,6) NULL`, and a `CHECK (helpful IS NULL OR role = 'assistant')` so only an
  assistant answer can be marked helpful. The no-LLM path writes `model IS NULL`, tokens `NULL` and
  `cost_cop NULL`: missing evidence is null, never `0`, and `model IS NULL` is what distinguishes "the
  LLM was never called" from "it was called and cost nothing" (SQL `SUM` ignores `NULL`, which is the
  budget behavior we want). Rejected: a separate `llm_usage` ledger table — it duplicates the same row
  and lets the ledger and the message disagree. Rejected: a monthly aggregate table — at 1 800
  questions/day (ADR-0007:9) a month is ~54 000 rows and one indexed `SUM` over `created_at` is not a
  problem to pre-compute. Two supporting indexes: `(conversation_id, created_at)` for the daily count,
  `(created_at)` for the monthly sum.

- **D-T12.2 — `kb_chunk.embedding` is full-precision `vector(<dim>)` with a cosine HNSW index, and the
  dimension is pinned by a provisional provider in T0.** docs/03 says `vector embedding`
  (docs/03-modelo-datos.md:361) and the index table says `HNSW(embedding vector_cosine_ops)`
  (docs/03-modelo-datos.md:558); docs win, and the corpus is 50 MB for ~10 000 fragments
  (`docs/02-estimaciones.md:90`), which is not worth a half-precision deviation. Consequence, stated
  because it reorders work: the dimension is a property of the embedding model, so **T0 pins a
  provisional provider** and T2 writes `vector(<that dim>)`; T8's eval either confirms it or lands a
  rewrite migration as its own work unit (changing an embedding model changes the column, and that
  change is never silent). Verified with ctx7 on 2026-10-06, recorded in
  `techcamp-v2-e12-blockers.md:74-82`: pgvector-python exposes `Vector(dim)` / `HALFVEC(dim)` with HNSW
  through `postgresql_ops={"embedding": "vector_cosine_ops"}`; the server side wants
  `maintenance_work_mem` raised before a bulk build and `pg_stat_progress_create_index` to watch it. For
  ~10 000 chunks one build is fine. Open in T0: index-before-load vs load-then-index during ingestion —
  T3 decides and records it.

- **D-T12.3 — The 10/day limit is enforced in **both** profiles; only the *budget* differs.** This
  resolves conflict C1. `docs/04-api.md:22` states the limit unconditionally, while ADR-0007:5 and
  ADR-0021:30 place *"el límite diario por usuario y el tope mensual"* in the production profile and give
  the seminar profile a USD 5 total. Docs win over the ADRs here because ADR-0007:5 itself scopes the
  daily limit to production, and an API contract that changed shape per profile would be two contracts.
  So: the endpoint enforces 10/day in both profiles with the same `429` + `Retry-After`; the seminar
  budget is the USD 5 total of ADR-0021:30 and the production budget is the monthly cap of ADR-0007:17.
  **T0 amends docs/04:22 to say so**, so the three documents agree instead of leaving the next reader
  to pick a winner. Owner-confirmable; this is the one decision in the set with a real fork.

- **D-T12.4 — The assistant stream is a per-request response body and is not ADR-0015's stream.**
  Resolves conflicts C2 and C3. `docs/04-api.md:227` returns `text/event-stream` from a `POST`;
  ADR-0015:13 defines SSE as the server→client fan-out on `GET /api/v1/stream` driven by one `LISTEN
  plot_events` connection per `api` process. Those are two different streams and must not be conflated:
  the assistant stream involves no `LISTEN`, no hub, no subscription, and it opens and closes with its
  request. docs/04 wins for the endpoint shape; ADR-0015 keeps its scope. Consequently docs/09:28's
  *"límite de 1 stream por usuario"* — a memory/descriptor control for the never-ending fan-out — does
  **not** apply to the assistant stream, and T0 amends docs/09:28 to say which stream it bounds.
  Two implementation consequences, both derived from code already in the repo: (1) `EventSource` can
  neither `POST` nor send the `Authorization` header, so the web reads the assistant stream with
  `fetch` + `ReadableStream` + the existing `createSseParser` (`web/src/lib/api/sse.ts`), the same shape
  as `useFarmEvents.ts:32-89`; (2) the server follows `telemetry/adapters/api/router.py:446-485` for the
  `StreamingResponse` shape and **must not** hold a `yield`-scoped `SessionDep` for the stream's
  lifetime — that route's docstring records the connection-starvation bug (`R3-stream-holds-db-session`),
  so the assistant's session is opened and closed around the work, not held open across tokens.

- **D-T12.5 — `kb_document` / `kb_chunk` have no `org_id`; `conversation` is scoped by `user_id` and the
  plot's org.** Resolves conflict C5. The corpus is institutional shared reference — the Boletín
  Agroclimático Nacional and the Caribbean mesas técnicas bulletins (docs/06:379) plus licence-reviewed
  guides — exactly like `municipality` / `model_version` / `risk_prediction`, whose docs/03:415 row
  already establishes the precedent that shared reference data has no `org_id` and is isolated where it
  leaves the server. `conversation` carries `user_id` + `plot_id` as specified (docs/03:363-366); its
  repository filters by `user_id` and access resolves through `resolve_plot_access`, the same gate every
  plot-scoped endpoint uses. **T0 writes both rows into docs/03's per-table decisions**, otherwise this
  reads as a violation of AGENTS.md:91 rather than an instance of the documented rule.

- **D-T12.6 — The outbound payload is a declared fact list; the send-list and the ban-list are code, and
  a test pins them.** docs/09:75 and ADR-0007:39 are the constraints: fragments and user text are data,
  the assistant has no writing tools, and only the minimum necessary leaves the server with no personal
  data. T4 builds the fact list from the facades; T5 turns it into the prompt. The rule is mechanical
  and therefore testable: the prompt builder emits the system prompt (docs/06:381-387 rules), the fact
  block, the retrieved fragments and the question — **and nothing else**. No user's phone, email or
  consent state; no technician's contact data; no `farm`/`plot` geometry. T5 carries a test that a fact
  block containing a phone-shaped or e-mail-shaped string never reaches the prompt. Consequence the owner
  must act on: ADR-0007:39 requires the third-party LLM to be disclosed **in the consent**, and
  `docs/04-api.md:33` (`POST /me/consent { version }`) takes the version from the client — so a consent
  version bump naming the provider is a coordination item for `identity`/docs, not an assistant file.
  T0 opens it as a follow-up issue rather than editing `identity` from this epic.

## Tasks

Checklist with stable ids. Forecasts are authored lines (additions + deletions, generated excluded) and
are stated **in deliverables first**, per the E10 calibration lesson: line forecasts in this repo
under-count by 2.2× (E10 T1/T2) to ~5× (E11, ~3 150 forecast vs ~14 700 actual) because tests were not
in the number, and here test/prod runs 1:1 or above. The line figure is a floor, not a target. Delivery
has **no line budget** (AGENTS.md: *never shrink a diff to fit one*).

- [x] **T0 — Decisions into owner docs** (D-T12.1…D-T12.6): docs/03 (the two `org_id` rows + the
  `message` columns and their CHECK), docs/04 (limit scope per profile, the assistant-stream paragraph,
  the endpoint detail), docs/06 §9 (cost/time persistence and the degradation branch), docs/07 (the
  screen's states under **Más**), docs/09 (scope the 1-stream limit; add the LLM cost bottleneck's
  trigger), docs/11 (the budget metric's source column). New ADR for the provisional provider pin
  (D-T12.2) and for the assistant stream (D-T12.4) if the owner prefers an ADR to a docs/04 paragraph.
  `docs/README.md:68` closes when T8 lands. **No code.** ~120 forecast. **Route: parent inline** —
  planning is never delegated; the `domain-modeling` skill is not installed in this repo, so the
  existing doc conventions are followed (E10 T0 precedent).

- [x] **T1 — Ports and test doubles**: `LLMClient.stream(messages) → tokens`, `Embedder.embed(texts)`,
  `KnowledgeRepository` (search by embedding with a similarity floor; document lookup for citations),
  conversation/message persistence, and a usage/quota port that answers *questions used today* and
  *month spend so far* as pure domain rules. Domain: the daily-limit arithmetic in `America/Bogota`
  (`shared.dates.local_today`), the prompt policy rules (docs/06:381-387), the citation shape. RED first
  for every one; the doubles are in-memory and assert what the tests then rely on. ~250 floor.
  **Route: delegated** (a fresh module, 6+ files, port contracts that four later tasks freeze —
  trigger: writer). Pass `find-docs`, `fastapi`, `pydantic` by path from `.atl/skill-registry.md`.
  **Depends T0.**

- [x] **T2 — Schema**: one revision on top of the current single head `b6e1c4a7f2d9`
  (`server/migrations/versions/b6e1c4a7f2d9_add_climate_risk_schema.py`, confirmed the only head of the
  26-revision graph): `CREATE EXTENSION IF NOT EXISTS vector` (following the `postgis`/`timescaledb`
  precedent at `9098dc0927a3:26` and `8c3983dc2dfd:79`; the seminar image already installs `vector` at
  `infra/postgres/init-extensions.sql:6`), `kb_document`, `kb_chunk` with
  `Vector(dim)` + `Index(postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})`,
  `conversation`, `message` with D-T12.1's columns and CHECK, the two supporting indexes, and the
  `pgvector` dependency pinned in `server/pyproject.toml` + `server/uv.lock`. RED: a test that fails on
  the missing tables/columns/index and on a second Alembic head (`justfile` fails on two heads — E10 T10
  had to rechain for exactly this). ~300 floor. **Route: delegated** (trigger: writer; migration +
  dependency + ORM). **Depends T0, T1.** Parallel with T4: disjoint files.

- [x] **T3 — Corpus ingestion**: chunker at ~500 tokens with overlap (ADR-0008:12), one `kb_document`
  per source with `source_url` + `license` + `kind` + `published_on` + `department`, ENSO state parsed
  onto `kb_document.enso_state` (docs/06:374), idempotent by `(source_url, sha256)` per ADR-0012's
  *(tipo, entidad, fecha)* rule, a procrastinate job plus `POST /dev/jobs/kb:ingest:run` (ADR-0021).
  **Gate before real data:** ADR-0008:32 — the corpus must be licence-reviewed *before* it is migrated.
  T3 ships the loader and loads only fixture documents; the real guides and bulletins land after the
  licence check, which is owner work (Open question 2). Index-before-load vs load-then-index is decided
  and recorded here (D-T12.2). ~500 floor. **Route: delegated** (trigger: writer; new job +
  registration in `worker.py`). **Depends T2.**

- [x] **T4 — Fact builders, retrieval use case and the no-LLM fallback**: sensors from the `telemetry`
  facade (`query_latest_plot_readings`; metric/value/time plus derived staleness — `ReadingPoint` carries
  only time and value, so `received_at`, the quality mark and the calibration version are a follow-up, not
  invented here; the facade already excludes quality-bit-2 and uncalibrated readings upstream), alerts from `alerts`
  (`list_open_alerts_for_plots` with `severity`, `opened_at`, `evidence`), irrigation from `irrigation`
  (`query_plot_recommendation` with `kind`, `rationale`, `depth_mm`/`duration_min` or the rainfed
  `advice`), seasonal climate from `kb_document` (latest bulletin for the farm's department, else the
  national one, with its publication date), and risk by **reusing `query_plot_risk`**
  (`risk/application/query_plot_risk.py:73`) instead of a second read of the risk tables — which also
  settles the assistant's first `risk` facade dependency. With the retrieved fragments (top 5,
  similarity ≥ 0.75, ADR-0008:12) this task also assembles the degraded no-LLM answer: the same facts
  plus the relevant documents, no provider call (docs/06:377). Org gate through `resolve_plot_access`.
  ~450 floor. **Route: delegated** (trigger: writer; five facades, the D-T12.6 send-list). **Depends T1.**
  Parallel with T2: T4 builds against the T1 ports with doubles and needs no T2 migration; the T2
  dependency holds only for live-kb integration tests, which move to T3.

- [x] **T5 — The streaming endpoint and feedback**: `POST /assistant/messages` returning
  `text/event-stream` — SSE frames carrying tokens and cited sources — and
  `POST /assistant/messages/{message_id}:feedback { helpful } → 204`. Session lifetime per D-T12.4, not
  held across tokens. Prompt assembly per D-T12.6 with the docs/06:381-387 rules as the system prompt,
  the fact block and the fragments framed as data. Persist the user message, the answer, the tokens and
  the cost (docs/06:365). Org isolation: a `conversation_id` or `plot_id` of another org/user is `404`.
  Spanish answers. ~500 floor. **Route: delegated** (trigger: writer; the endpoint is the epic's
  integration point). **Depends T4.** Parallel with T6's non-router half only if T6 is split; otherwise
  sequential.

- [ ] **T6 — Limits, budget and the cost metric**: the 10/day limit per user in `America/Bogota` with
  `429` + `Retry-After` (seconds; a `Retry-After` HTTP-date would need a clock the client cannot
  assume), the degradation branch when the limit or the budget is exhausted (docs/06:356-359), the USD 5
  seminar cap (ADR-0021:30) and the monthly cap (ADR-0007:17), and the metric. **Where the metric lives
  is a decision, not an assumption:** `docs/09:86` and `docs/11:132` name `llm_cost_cop_total`, but
  `/metrics` does not exist yet (`server/src/techcamp/main.py` has only `/health`; `prometheus-client` is
  not a dependency). So T6 makes `gasto del mes / tope` **derivable from `message.cost_cop`** and records
  the metric name for whoever builds `/metrics`; adding a Prometheus client and an endpoint here would
  pull E14's work into this epic. The 120 req/min limiter is filed as a follow-up (Scope, Out).
  ~350 floor. **Route: delegated** (trigger: writer; limit + budget logic with a port). **Depends T5.**

- [ ] **T7 — Web screen** (`impeccable`): `web/src/features/assistant/` in the docs/07 layout
  (`api/` `containers/` `components/`), the streamed `POST` read with `fetch` + `ReadableStream` +
  `createSseParser` (D-T12.4), the message list, the composer, the cited-sources list, and the three
  states a producer must be able to distinguish: streaming answer, degraded no-LLM answer, and
  rate-limited (`429` → *"mañana podés seguir preguntando"*, not a raw status). Offline is an explicit
  state (ADR-0005: this screen needs the network and must say so rather than pretend). Route wiring
  under **Más** (`web/src/app/routes.tsx`, where `MasTab` already lists the indicators entry) at the
  path docs/07:129 reserves. `schema.d.ts` regenerated from the merged OpenAPI before the web lane starts
  (E11's precedent: regenerating it on the epic branch is what made two lanes parallelizable). Spanish UI
  copy from docs/07 and the glossary. ~800 floor. **Route: delegated + `impeccable`** (trigger: writer,
  UI). **Depends T5.** Parallel with T6 (disjoint files: `web/`).

- [ ] **T8 — Eval and the provider decision**: 30 real Caribbean agronomic questions graded by an
  agronomist on accuracy, citations and clarity (ADR-0007:15) — **the pass threshold is not written in
  any doc**, so the threshold is itself an owner decision (Open question 1) and must be fixed before the
  run, not read off the results. The outcome lands as: the provider ADR, `docs/README.md:68` closed, the
  real prices written back into `docs/02-estimaciones.md:78` (conflict C4), a dimension rewrite
  migration if the chosen embedding model differs from D-T12.2's pin, and a decision on hybrid search if
  exact-term questions fail (ADR-0008:19). ~150 of doc, plus human grading time. **Route: owner for the
  grading, parent for the docs**; the harness that replays the 30 questions against a fixed corpus is
  delegated and lands with T8's first commit. **Depends T5.** Parallel with T6 and T7.
  - **T8 plan (lane `e12-t8`, 2026-10-08).** Two work units. **U1 — the harness and its fixtures,
    own commit, before any verdict:** a deterministic replay of the closed 30-question set over the
    pinned fixture corpus, through the *real* `retrieve_fragments` and the *real* `build_prompt`, with
    `SeminarHashEmbedder` and a `FakeKnowledgeRepository` seeded from `load_corpus`/`chunk_text`.
    The harness **produces the graded input and nothing else**: no score, no threshold, no verdict —
    those are owner work (Open question 1) and a structural guard asserts their absence from the
    report shape. **U2 — the docs**, which needs the verdict and therefore waits for the owner.
  - **Surface deviation, flagged and not resolved here:** the brief's own *Done means* requires
    "ODD updated (checkbox + Review entry)", but `odd/tasks/**` is absent from its *Allowed edit
    surfaces*. Read as: the done-criterion authorizes this one file. Nothing outside the five listed
    surfaces plus this feature doc is touched.

- [ ] **T9 — Close**: `just gate-full` **once** on a clean database, follow-up issues for every
  non-blocking RDD finding, the delivery plan (chained-to-main PRs, one per work unit, `size:exception`
  — slices pass 400 lines and this repo has no line budget), and the epic close. ~100. **Route: parent.**
  **Depends T1–T8.**

**Lanes.** Wave 1: T1. Wave 2: T2 ‖ T4 (disjoint: migrations+ORM vs application). Wave 3: T3. Wave 4:
T5. Wave 5: T6 ‖ T7 ‖ T8 (server limits vs web vs eval docs; T6 and T5 share the router, so T6 starts
only after T5 merges). Wave 6: T9. Parallel lanes only with disjoint files, each in its own worktree,
branch and `just db-up`.

## Authorized scope

Writable surfaces per task, and where a writer must stop and ask instead:

| Task | May write | Must stop and ask for |
| --- | --- | --- |
| T0 | `docs/03`, `docs/04`, `docs/06`, `docs/07`, `docs/09`, `docs/11`, `docs/README.md`, `docs/adr/*` (new), `AGENTS.md` if the Layout needs it, this file | Anything outside `docs/` + `odd/tasks/`; changing an *accepted* ADR's meaning without a new one |
| T1 | `server/src/techcamp/assistant/{domain,application}/**`, `server/tests/assistant/**` | `server/src/techcamp/shared/**` (cross-cutting) — ask |
| T2 | `server/migrations/versions/<one new revision>.py`, `server/src/techcamp/assistant/adapters/orm.py`, `server/tests/assistant/**`, `server/pyproject.toml` + `server/uv.lock` | A second Alembic revision in the same task; an existing revision's `down_revision` unless the head moved and the rechain is the whole task |
| T3 | `assistant/adapters/jobs.py`, the loader, `server/src/techcamp/worker.py` (registration only), `server/src/techcamp/main.py` (the `/dev` router), `server/tests/assistant/**`, corpus fixtures | `ml/`; downloading real corpus documents before the licence check |
| T4 | `assistant/application/**`, `assistant/domain/**`, `server/tests/assistant/**` | Another module's facade (`farms`, `telemetry`, `irrigation`, `alerts`, `risk`) — a missing export is a change *there*, so ask |
| T5 | `assistant/adapters/api/**`, `server/src/techcamp/main.py`, `server/tests/assistant/**` | `web/`; the `/api/v1/stream` route or the telemetry hub |
| T6 | `assistant/**` (limit, budget, ledger read), `server/tests/assistant/**`, `shared/config.py` for the provider keys | Adding `prometheus-client` or a `/metrics` endpoint; the API-wide 120 req/min limiter |
| T7 | `web/src/features/assistant/**`, `web/src/app/routes.tsx`, `web/src/lib/api/schema.d.ts` (regenerated), `server/openapi.json` if regenerated | `web/src/design-system/**` outside an `impeccable`-governed token/primitive change; another feature's screen |
| T8 | `docs/02`, `docs/README.md`, `docs/adr/*`, eval fixtures + results under `server/tests/assistant/` | `ml/`; any change to `model_version` or the risk serving path |
| T9 | `justfile`, `.github/workflows/` (only if CI's path filters genuinely miss the new files — they do not: `server/**`, `web/**` are covered at `.github/workflows/ci.yml:45`), this file | Any source change; that is a new task with its own receipt |

No writer opens or merges a PR, and no writer edits another module's internals to make its own task
easier.

## Acceptance criteria

Each item names the evidence that closes it. Tests are named by behavior, not written here.

- [ ] Every answer carries **citations** and, when it speaks about risk, the **`model_version`** that
  produced the prediction — `model_version.version` plus its validation metric, and the explicit
  *"linea base"* mark when the served version is one (docs/06:375, `docs/04-api.md:232`).
- [ ] The served baseline's `low` is **never narrated as a verdict**: the answer reports the served
  version is `climatology_month` with `thresholds={}` (D-T9.2), so `low` is missing calibration, not a
  finding. A test asserts the assistant's Spanish copy marks it and never asserts a severity judgement
  the system did not make.
- [ ] **No prediction → "not available"**, never an invented value: `drought` has no registered
  predictor (D-T9.3) and a plot without a cell has no predictions at all; the risk fact is absent and
  the prompt says the evidence is missing rather than naming a severity.
- [ ] The **11th question of the day answers `429` with `Retry-After`** (docs/04-api.md:22), the tenth
  succeeds, and the counter resets at `America/Bogota` midnight — including across the UTC-midnight
  offset, which is the case a naive `date(created_at)` test misses.
- [ ] **Budget exhausted → no LLM call at all**: the provider double is not invoked, the degraded answer
  (facts + relevant documents) is returned, and the month-to-date sum comes from `message.cost_cop`.
  Both caps: USD 5 seminar (ADR-0021:30), monthly cap production (ADR-0007:17).
- [ ] **Another org answers `404`**: a `conversation_id` and a `plot_id` of another org or another user
  answer `404`, never `403` and never an empty `200` (docs/04-api.md:16); repository-level isolation
  tests per docs/09.
- [ ] **Anti prompt-injection**: a fragment and a user question carrying instruction-shaped text are
  treated as data — the assistant follows the system prompt, invents no dose, and follows no instruction
  found in a chunk (docs/09:75, docs/06:386). The D-T12.6 send-list test: a fact block containing a
  phone- or e-mail-shaped string never reaches the prompt (ADR-0007:39).
- [ ] **Retrieval honors top-5 and similarity ≥ 0.75** (ADR-0008:12): fewer than five qualifying
  fragments is a legitimate answer with what exists, and a 0.74 fragment is excluded. The threshold is a
  named constant, not a literal in a query.
- [ ] Facts are assembled **before** the single call, and the flow is deterministic: one
  `LLMClient.stream` invocation per request, no tool loop, no framework (ADR-0007:26).
- [ ] The answer obeys the docs/06:381-387 rules: plain Spanish, glossary vocabulary
  (`docs/00-glosario.md`), cites the source, says when evidence is insufficient, and refers to the ICA
  label and the technician instead of inventing a dose.
- [ ] `POST …:feedback` stores `helpful` and a `204`; a message of another user is `404`.
- [ ] Every task's scoped gate is green and RDD ran per work-unit commit; `just gate-full` ran **once**
  in T9.

## Checks

Run from the repo root with `just`. Each worktree brings up its own Postgres, so parallel lanes never
share a database.

| When | Command | What it covers |
| --- | --- | --- |
| Once per worktree, before any lane starts | `just db-up` | This worktree's database. Never `just db-reset` mid-epic — a lane's rows are evidence |
| Per work-unit commit | `just gate-fast` | ruff, ruff format, mypy, `lint-imports`, exactly one Alembic head, ast-grep rules, eslint, tsc |
| Per task, only the tests it touches | `just gate server/tests/assistant` · `just gate web/src/features/assistant` · `just gate server/tests/assistant web/src/features/assistant web/src/app` | Static checks plus **exactly** the named tests, node ids included. No paths = no tests |
| When touching another module's facts | `just gate server/tests/risk server/tests/irrigation server/tests/alerts server/tests/telemetry` | E6/E7/E10 consumers: a change in a shared facade or a cross-module read shows up here, not at T9 |
| Before slicing the lane into PRs | `just gate-lane origin/main` | Static checks on **every** commit of `origin/main..HEAD`; gating only HEAD misses a middle commit that breaks (AGENTS.md) |
| Once, in T9 | `just gate-full` | Clean database, then everything CI runs |
| Optional, T9 | `just gate-release origin/main` | Pre-PR rule steps 1–4 plus extra random server seeds |

Never run a bare `pytest` in `server/`: it falls back to the seminar stack's database on 5432. Targeted
runs are `uv run pytest tests/<module>/test_x.py::test_name`; web is `npm test -- --run <file>` (`npm
test` alone is watch mode). Server tests run in random order: reproduce a failure with the seed from the
header.

## Forecast and delivery

- **Delivery strategy: `ask-on-risk` by default** — one work unit per chained PR, stacked to `main`,
  merged in order, each with its own RDD receipt against the previous reviewed boundary. Because no
  slice of this epic fits 400 authored lines and AGENTS.md states the repo has **no line budget**, the
  slices carry `size:exception` rather than being shrunk. Push, PR opening and merging stay with the
  parent/owner.
- **Forecast: ~3 400 authored lines as a floor, ~10 tasks, 13–16 PRs.** Treat it as a floor for the
  reasons above: E11 measured ~5× and E10's schema task alone measured 5.9×. If T2, T4 or T7 crosses its
  floor, the writer reports the overrun at the moment it crosses rather than grinding past it (E10's
  lesson).
- Merge commits, never squash, so the receipt shas keep existing.

## Hard requirements

| # | Requirement | Source |
| --- | --- | --- |
| H1 | Ports `LLMClient` / `Embedder` only; the LLM explains and never computes irrigation, risk or doses | ADR-0007:14,16 |
| H2 | Deterministic single-call flow: gather facts → retrieve → one call. No agent framework, no Ollama | ADR-0007:13,26 |
| H3 | Facts assembled deterministically from facades before the call, and the LLM must cite them | docs/06:369-375 |
| H4 | Top 5 fragments, similarity ≥ 0.75, HNSW cosine | ADR-0008:12, docs/03:558 |
| H5 | Every document records source and licence; the corpus is licence-reviewed before migration | ADR-0008:12,32 |
| H6 | Degrade to a fact-only answer with no LLM when the limit or budget is exhausted | docs/06:356-359,377 |
| H7 | Never invent agrochemical doses — refer to the ICA label and the technician | docs/06:386 |
| H8 | 10 questions/day per user; `429` with `Retry-After`; monthly budget cap | docs/04:22, ADR-0007:17 |
| H9 | RAG chunks and user text travel as data; no writing tools; text output only | docs/09:75 |
| H10 | Minimum data to the provider, no personal data, disclosed in the consent | ADR-0007:39 |
| H11 | Org isolation; another org's resource is `404` | AGENTS.md:91, docs/09:71, docs/04:16 |
| H12 | Plain Spanish, glossary vocabulary, sources cited, "insufficient information" stated | docs/06:381-387, docs/00 |
| H13 | The corpus is the ENSO/seasonal source: the national bulletin plus the Caribbean mesas | docs/06:374,379 |
| H14 | Every repository filters by `org_id` (or by owning user where the docs say so) | AGENTS.md:91, D-T12.5 |
| H15 | Tests run with a `429`/limit/budget path; domain tests are pure; doubles only at ports | AGENTS.md §Testing |

## Conflicts to settle in the docs (docs win, AGENTS.md rule 2)

T0 resolves all six in the same work unit as the doc change. Numbering is preserved from
`techcamp-v2-e12-blockers.md:44-57` so the two documents read as one record.

| # | Conflict | Line A | Line B | Resolution |
| --- | --- | --- | --- | --- |
| C1 | Daily limit always vs production-only | `docs/04-api.md:22` | `docs/adr/0007-llm-por-api.md:5`, `docs/adr/0021-perfil-seminario-local.md:30` | **D-T12.3**: the limit is enforced in both profiles with one `429` contract; only the budget differs (USD 5 seminar, monthly cap production). docs/04:22 amended. Owner-confirmable |
| C2 | SSE direction: ADR-0015 defines SSE as server→client on `GET /api/v1/stream` | `docs/adr/0015-tiempo-real-sse.md:6,13` | `docs/04-api.md:227` (POST returning `text/event-stream`) | **D-T12.4**: both are true and they are different streams. docs/04 wins for the endpoint shape; ADR-0015 keeps `/api/v1/stream`. No `LISTEN`, no hub on the assistant path |
| C3 | "1 stream per user" covers a stream the API contract does not describe | `docs/09-cuellos-de-botella.md:28` | `docs/04-api.md:227` | **D-T12.4**: that limit bounds the never-ending fan-out; the assistant stream is per-request. docs/09:28 amended to name which stream it bounds |
| C4 | Reference model fixed by cost (Haiku 4.5, USD 1/5) while the provider is pending | `docs/02-estimaciones.md:78` | `docs/adr/0007-llm-por-api.md:14-15` | Not a contradiction: docs/02:78 itself says *"los precios se reconfirman al implementar"*. T0 pins a provisional provider for the embedding **dimension** (D-T12.2); T8 writes the chosen provider's real prices back into docs/02:78 and closes `docs/README.md:68` |
| C5 | The kb corpus has no `org_id`, against the rule that every repository filters by `org_id` | `docs/03-modelo-datos.md:347-362` | `AGENTS.md:91`, `docs/09-cuellos-de-botella.md:71` | **D-T12.5**: `kb_*` is shared institutional reference like `municipality`/`risk_prediction`, with the precedent already written at docs/03:415; `conversation` is scoped by `user_id` + the plot's org through `resolve_plot_access`. Both rows added to docs/03 |
| C6 | TDD per behavior change vs "one bounded run at the end" | `AGENTS.md:100` | Owner instruction to run one bounded pass at the end | The repo rule stands: RED → GREEN → REFACTOR with tests next to the behavior, one scoped `just gate <paths…>` per work unit, `just gate-full` only at epic close (T9). This document changes no source, so it runs no pytest |

## Open questions (owner)

1. **Provider, embedding model, and the eval pass threshold.** ADR-0007:15 fixes the method (30 real
   Caribbean questions, graded by an agronomist on accuracy, citations and clarity) but no doc states
   what score promotes a provider. Fix the threshold **before** the run or T8 has no verdict. Pin a
   provisional provider now for D-T12.2's dimension; T8 confirms or rewrites the column.
2. **Corpus licence.** ADR-0008:32 requires review before migration. Which guides may be included, and
   who signs it off? T3 loads only fixtures until this is answered.
3. **Retention and deletion of `conversation` / `message`.** Undecided anywhere. Relevant to
   `DELETE /me` (docs/04:36) and to the fact that an assistant transcript is the user's own words.
4. **Membership check beyond `CurrentUserId`.** Does `POST /assistant/messages` need more than the
   standard authenticated user plus `resolve_plot_access`? E12's reading: no — any member of the plot's
   org may ask, the same as every plot-scoped read. Confirm or overrule.
5. **Idempotency.** `docs/04-api.md:19` says every `POST` that creates something accepts
   `Idempotency-Key`; `farms` and `telemetry` deliberately deferred it. A streamed answer is not
   replayable, so E12's position is that it does not apply here — worth one line in docs/04 either way.

## Review (RDD)

- **T1 — `review-ac9e8e40979d08c2` — aprobado y reconocido.** Target `sha256:5bdc4edd…`, lente
  `review-reliability` (medium, 2811 líneas, presupuesto de corrección 200). Recibo quemado con
  `gentle-ai.review-acknowledged/v1`, `consumed_revision sha256:89d40da5…`, `authority: burned`,
  `delivery: ordinary-repository-policy`.
  - Un lineage previo (`review-ac30d09f29b4d91f`) encontró **R3-QUOTA-RACE** (CRITICAL): la admisión de
    cuota era un read/check no atómico y el puerto de cuota no tenía reserva. La corrección fue
    `UsageQuotaPort.admit_question` (reserva atómica) + `evaluate_quota` usándola + doble in-memory +
    test de carrera, en 170 líneas de diff, admitidas por el plan de corrección.
  - **Lección de proceso:** `capture-correction-plan` es un forecast **pre-edit**. Editar antes de
    declarar el plan mueve el target y el binding emitido deja de coincidir; el ciclo correcto es
    plan → edición → validación. Ese lineage quedó en `correction_required` porque su binding de
    validación dirigida apuntaba al target del candidato de corrección y el controller lo rechazó
    (`review repair --preflight` lo declara `unsupported`), así que se resolvió con un START fresco
    sobre el árbol ya corregido.
  - **Hallazgos no bloqueantes** (WARNING, `informational`; ninguno reabre la revisión; van al
    issue `review-follow-up` #302 de la ronda): `R3-BLANK-MODEL-EVIDENCE`, `R3-CITATION-KIND-STRING-BYPASS`,
    `R3-MESSAGE-CONVERSATION-MISMATCH`, `R3-QUOTA-RACE-DOUBLE`, `R3-QUOTA-REASON-PRECEDENCE`,
    `R3-RESERVATION-DOUBLE-COUNT`, `R3-UNSTABLE-FRAGMENT-IDS`, `R3-USER-MESSAGE-LLM-EVIDENCE`. Los
    que tocan el código de T1 (`BLANK-MODEL-EVIDENCE`, `CITATION-KIND-STRING-BYPASS`,
    `USER-MESSAGE-LLM-EVIDENCE`, `QUOTA-REASON-PRECEDENCE`) se corrigen en su propio commit con
    `Refs #302`, diferidos hasta que T2/T4 aterricen (decisión del dueño 2026-10-07); los que son
    solo del doble de prueba se resuelven al escribir el adaptador.
- **T3 — `review-0d72f97fb50a0ff3` — aprobado y reconocido.** Lente `review-reliability`
  (medium, 5 archivos/240 líneas, sin corrección). Recibo quemado con
  `gentle-ai.review-acknowledged/v1`, `delivery: ordinary-repository-policy`. Rebanadas `d90853c`
  (loader + fixtures) + `f44279d` (job + registro + router /dev); `just gate
  server/tests/assistant` → 197 passed. D-T12.2 decidido: **index-before-load** (la migración T2 ya
  crea el HNSW en tabla vacía; el corpus ~10k fragmentos está un orden de magnitud bajo el umbral de
  rebuild; el job nunca corre DDL — ver docstring de `kb_ingest.py:47-60`). Hallazgos a #305 (2 WARNING
  + 1 SUGGESTION, `Refs #305`).
- **T4 — dos slices aprobados y reconocidos (recibos quemados).**
  - **Slice 1 — `review-4dbdecc82fb95e75`** (`f0ef15f`, padre `a127acb`): lente `review-reliability`
    (medium, 6 archivos/2138 líneas, presupuesto de corrección 200). Un intento previo sobre el rango
    completo (`a127acb..98bf0f4`, 14 archivos/4278 líneas) fue rechazado con
    `lens_context_budget_exceeded` **sin crear autoridad**: `assess --committed-only` resuelve
    `base-ref..HEAD`, así que revisar un slice exige `git checkout --detach <sha>` del slice.
    Hallazgo **R3-MUTATION-LEAK** (CRITICAL, determinista): la mitad "`metrics` se puede mutar" era
    falsa (`facts.py:281-282` ya copia `metrics` y `top_factors`); la verdadera: `_read_only` hacía
    `dict(values)` sin protección y un mapping que falla al copiarse lanzaba excepción desde el
    constructor, violando el "Never raises" del módulo (`fact_safety.py:275-277`). Corregido con TDD
    (RED `RuntimeError: boom` → GREEN), 55 líneas. **Incidente de integridad:** el commit de la
    corrección se creó con HEAD detached en `f0ef15f` (exigido por `--committed-only`) y quedó
    huérfano (`83c9e6c`); se re-aterrizó al tip con cherry-pick como `7a81758`, con autorización del
    dueño. **Lo verificado el 2026-10-07 con los árboles delante, porque la frase anterior era
    imprecisa:** el validador inspeccionó `01e9068126b12fe00920d1f58c68b007956f5789`, que es el árbol
    de **`83c9e6c`** (slice 1 + el fix) y **no** el del tip, que es
    `1b2f6dac78e31f8095f6b3ff856b7dbc49c26a2d` (`7a81758`). El parche sí es el mismo —
    `git diff f0ef15f 83c9e6c` y `git diff 98bf0f4 7a81758` son idénticos (54+/1−, 2 archivos) — así
    que los bytes revisados aterrizaron; pero **el árbol revisado no es el árbol del tip y no debe
    leerse como si lo fuera**: `349ac8a` y `98bf0f4` tienen su propio estado (slice 2 revisado y
    quemado; slice 3 `under_budget`). Verificación:
    `just gate server/tests/assistant` → 233 passed. Recibo quemado
    (`gentle-ai.review-acknowledged/v1`, `authority: burned`).
  - **Slice 2 — `review-7e441487c1135f12`** (`349ac8a`, padre `f0ef15f`): lente `review-reliability`
    (medium, 6 archivos/1880 líneas). Sin ronda de corrección: ningún hallazgo CRITICAL/BLOCKER.
    Verificación: `just gate server/tests/assistant` → 228 passed. Recibo quemado
    (`gentle-ai.review-acknowledged/v1`, `authority: burned`). El relay del revisor fue rechazado dos
    veces por el transporte (`opencode_review_transport_relay_refused`, `provider_failed`) en dos
    modelos distintos; se resolvió con el camino `--input` documentado por el contrato
    (`capture-result --preflight` → `admission_decision: completed`, luego captura real).
  - **Slice 3 — `98bf0f4`** (padre `349ac8a`): `assess` → medium, `review_due: false`,
    `review_due_reason: under_budget` (2 archivos/260 líneas). **Sin revisión debida**; queda
    pendiente en el slice. Re-assess tras el cherry-pick (rango `349ac8a..7a81758`, 4 archivos/315
    líneas) → `under_budget` otra vez, sin transición. **Re-assess del 2026-10-07, con el HEAD
    adjunto y sin detach** (`assess --base-ref 349ac8a --committed-only`): `risk: medium`,
    `review_due: false`, `review_due_reason: under_budget`, `candidate.kind: base-diff`,
    `candidate.consumed: false`. No hay `next_transition` que ejecutar, así que **no hay recibo que
    quemar y no se fabricate uno**: esos 315 líneas quedan *pendientes en el corte*, revisados solo
    si el dueño decide pedir una revisión independiente de ese rango (un target quemado no se
    reinicia solo).
  - **Evidencia de los dos recibos, verificada en disco y no de memoria:** existen los dos registros
    de consumo terminal en el git dir común del repo,
    `.git/gentle-ai/review-transactions/terminal-consumption/v1/` — `4e8ee852….json` → lineage
    `review-4dbdecc82fb95e75`, target `sha256:f9f72fa2…`; `ed7b0a03….json` → lineage
    `review-7e441487c1135f12`, target `sha256:4afdb048…`; los dos con la identidad de repositorio
    `sha256:57edb8cb…`. Es la razón por la que un re-review de esos cortes no es una opción
    (`target_already_acknowledged`): su autoridad ya está consumida. Nota de alcance: el receipt de
    T2 (`review-f1315ed728b0e08a`) pertenece a la lane `e12-t2` y **no** aparece en este git dir
    común, porque esa lane se cierra en su propio worktree.
  - **Hallazgos no bloqueantes** (16 WARNING, `informational`; ninguno reabre su revisión): los 4 del
    slice 1 (`R3-DETERMINISM-UNPROVED`, `R3-NEGATIVE-ASSERT-GAP`, `R3-PATTERN-GAP`, `R3-README-GUARD`)
    y los 12 del slice 2 (fidelidad de dobles de prueba, fronteras no ejercitadas y rutas defensivas
    sin probar) van al issue combinado **#304** (`review-follow-up`, `type:chore`, `area:server`).
    Los que tocan código de T4 se corrigen en commit propio con `Refs #304`; los que son solo del
    doble se resuelven al escribir el adaptador.
- **T2 — `review-f1315ed728b0e08a` — aprobado y reconocido.** Target `sha256:b604fb82…`, lente
  `review-reliability` (medium, 3 archivos/486 líneas, sin ronda de corrección). Recibo quemado con
  `gentle-ai.review-acknowledged/v1`, `delivery: ordinary-repository-policy`. Sin envelope de consent
  (el STATUS ligado devolvió collect sin envelope: sin decisión humana que respetar). Punto ciego:
  `server/pyproject.toml` + `server/uv.lock` (pin `pgvector>=0.4.0`) no entraron al candidato por ya
  versionados → commit aparte `c0d0b50`; `assess` dice medium/`under_budget`, pendiente en el slice.
  Verificación en lane `e12-t2`: `just gate server/tests/assistant` → 136 passed (seed 1314030592);
  unidades `4681cf2` (schema) + `c0d0b50` (deps). Fricción de worktree fresco (fuera de superficie,
  mecánica y anotada): `ml/uv.lock` se rancia por el path-dep a `server` (regen) y `web/node_modules`
  ausente (`npm ci`).

- **T8 — `review-3c128bea98f8df25` — aprobado y reconocido (solo el corte de hermeticidad).**
  Target `sha256:43b7c307…` (2 archivos/26 líneas, medium), lente `review-reliability`, **0 hallazgos**.
  Recibo quemado con `gentle-ai.review-acknowledged/v1`, `authority: burned` (2026-10-23). El corte
  revisado es `d7cf535` con `--base-ref` en su padre `6d429bc`: la lección de la sesión del 2026-10-08
  (el candidato resolvía la lane entera sin base explícita) quedó aplicada y el candidato llegó
  acotado a la unidad. **El resto de T8 no quema recibo por decisión de alcance del dueño
  (2026-10-23):** la verificación técnica es del agente `gentle-ai-verify` y el review RDD se usa solo
  cuando el veredicto lo pida; el verify del 2026-10-23 (abajo) volvió sin bloqueadores, así que no se
  abrieron más lineages. Un lineage abierto por error bajo la lectura por-commit
  (`review-44c48cffdf1c9ab0`, sin lentes ni resultados) queda en-flight sin autoridad capturada, igual
  que los `reviewing` huérfanos de rondas anteriores; nada está aprobado ni quemado en él.

Per work-unit commit: `gentle-ai review assess` against the previous reviewed
boundary, one lineage per unit, blocking findings in a bounded correction, non-blocking ones to one
GitHub issue per review round (`review-follow-up`, `epic:e12`, `area:*`, `type:*`) linked from here with
`Refs #N`. This document's own commit is a passive doc-only candidate.

Lessons carried from E10's RDD lifecycle, so they are not rediscovered: scope a candidate with
`assess --base-ref <parent> --committed-only` and follow the returned transition (the selectorless
`inspect` freezes the whole epic branch and has burned two lanes); never split a generated lockfile
into a review base (T1's `ml/uv.lock` burned an attempt); a new target does not give an independent
lineage; no approved receipt means *not reviewed*, never approved.

## Progress / evidence

- 2026-10-06: pre-implementation assessment landed as `techcamp-v2-e12-blockers.md` (`f61adc5` on
  `docs/e12-blockers-assessment`, one commit over `main` `c077973`): verdict **E12 can start**, 0
  blocking issues of 129 (77 open), 6 blockers that are repo gaps or undecided docs, 5 degradations,
  6 contract conflicts, and the T0–T9 plan this document turns into work units.
- 2026-10-06: state verified directly, not from the handoff. Alembic: 26 revisions, **one head**,
  `b6e1c4a7f2d9` (`add_climate_risk_schema.py`, `down_revision = 43c9c5cc68c9`) — the graph was built
  from the files, not recalled. `server/src/techcamp/assistant/` = 4 empty `__init__.py`; no
  `server/tests/assistant/`. `vector` appears in no migration (only `postgis` at `9098dc0927a3:26` and
  `timescaledb` at `8c3983dc2dfd:79`) and is installed at image init
  (`infra/postgres/init-extensions.sql:6`). `pgvector` absent from `server/pyproject.toml`.
  `techcamp.assistant` already whitelisted at `server/pyproject.toml:109`, so **no import-linter change**.
  CI path filters already cover `server/**` and `web/**` (`.github/workflows/ci.yml:45`), so **no CI
  change**. `429`/`Retry-After` appear in `server/src` only inside the Open-Meteo clients.
  `prometheus-client` is not a dependency and `/metrics` does not exist (`main.py` serves only `/health`),
  which is why D-T12.1 routes the budget through a `message.cost_cop` `SUM` rather than a metric.
- 2026-10-06: reuse confirmed by reading the code that will be reused — `resolve_plot_access`
  (`farms/application/manage_plots.py:29`, 25 callers, the gate every plot-scoped read uses),
  `query_plot_risk` (`risk/application/query_plot_risk.py:73`, which already returns
  `is_baseline` and `metrics` so D-T12.1's acceptance criterion needs no new read), the SSE route
  (`telemetry/adapters/api/router.py:446-485`) and its `R3-stream-holds-db-session` docstring,
  `CurrentUserId` (`identity/adapters/api/deps.py:31-49`), the SSE parser and the `fetch` +
  `ReadableStream` precedent (`web/src/lib/api/sse.ts`, `web/src/lib/api/useFarmEvents.ts:32-89`),
  `MasTab`'s existing entry list (`web/src/app/routes.tsx`), and `risk/adapters/repositories.py:3-8`
  (no `org_id`) against `alerts/adapters/repositories.py:3-8` (filters by it).
- 2026-10-06: pgvector facts already verified through ctx7 (`techcamp-v2-e12-blockers.md:74-82`); not
  re-run here.
- 2026-10-06 — **T0, decisiones escritas en sus documentos dueño.** Sin código: este commit es solo
  documentación pasiva, así que no corre pytest ni `gate-full` (C6). Diff: 7 documentos modificados
  (`docs/03`, `docs/04`, `docs/06`, `docs/07`, `docs/09`, `docs/11`, `docs/README.md`) y 2 ADR nuevas
  (`0026-proveedor-provisional-e12.md`, `0027-stream-asistente-e12.md`), +55/−8 sobre los modificados.
  - **D-T12.1** → [docs/03](../docs/03-modelo-datos.md): columnas `created_at`, `model` y `cost_cop` en
    `message`, `CHECK (helpful IS NULL OR role = 'assistant')`, y la sección
    "`kb_document`, `kb_chunk`, `conversation` y `message`". Los dos índices de apoyo
    (`conversation_id, created_at`) y (`created_at`) están en la tabla de índices.
  - **D-T12.2** → **ADR-0026** (nueva). `vector(<dim>)` con la dimensión fijada por el proveedor
    provisional, y `docs/03:558` anotado.
  - **D-T12.3** → `docs/04:22`: los 10/día en los dos perfiles, solo el tope cambia por perfil.
    Sigue marcado owner-confirmable.
  - **D-T12.4** → **ADR-0027** (nueva) + párrafo nuevo en `docs/04` (sección "Riesgo, métricas y
    asistente") + nota bajo la tabla de cuellos de botella de `docs/09`.
  - **D-T12.5** → `docs/03`: filas `org_id` para `kb_*` y `conversation`, con el precedente de
    `municipality`/`risk_prediction` (`docs/03:415`) citado en la propia fila.
  - **D-T12.6** → sin cambio de documento: la lista de envío es código y la lleva T5 con su test. Queda
    abierto el ítem de consentimiento por version bump, porque tocar `identity` desde este épico está
    fuera de superficie (ver abajo).
  - `docs/11:132` ahora dice que el gasto es `SUM(message.cost_cop)`; **no se creó `/metrics`**
    (`prometheus-client` sigue sin ser dependencia). `docs/README:68` **sigue abierto**, ahora con la
    nota de que ADR-0026 es solo un pin provisional.
- 2026-10-06 — **Restricción del dueño aplicada a D-T12.2: modelos baratos, nada de SDK Anthropic, tope
  DeepSeek.** Verificado con ctx7 (biblioteca `/websites/api-docs_deepseek`): la API es compatible con
  OpenAI (`OpenAI(api_key=…, base_url="https://api.deepseek.com")`), `POST /chat/completions` con
  `Authorization: Bearer`, streaming SSE con líneas `data:` y cierre `data: [DONE]`, y `usage` con
  `prompt_tokens`/`completion_tokens` en el último chunk (de ahí sale `cost_cop`). **La misma
  documentación no expone ningún endpoint de embeddings** (búsqueda sin resultados), así que el
  embedding es una decisión separada y no un afterthought.
  - **Embeddings:** ADR-0026 deja **dos** opciones baratas con dimensión explícita y las marca
    owner-confirmable: A `intfloat/multilingual-e5-small` (384) y B `BAAI/bge-m3` (1024), ambas MIT y
    locales. **T2 no puede escribir `vector(<dim>)` hasta que el dueño confirme una**; la dimensión y la
    licencia se reconfirman contra la ficha del modelo antes de la migración.
  - **Costos:** `docs/02:78` (Haiku 4.5, USD 1/5) queda obsoleto y **no se editó** (fuera de
    superficie). ADR-0026 deja escrito que **T8** escribe los precios reales de `deepseek-chat` y del
    embedding elegido.
- 2026-10-06 — **Superficie faltante, reportada y no editada:** `docs/adr/README.md` (el índice del
  registro de ADR) no estaba en la lista de superficies autorizados de T0, así que **ADR-0026 y ADR-0027
  no están registradas en esa tabla**. Hace falta una línea por cada una, o abrir T0 de nuevo con esa
  superficie. También quedó fuera, por lo mismo, la versión de consentimiento que nombra al proveedor
  ([ADR-0007:39](../docs/adr/0007-llm-por-api.md)) — eso es un ítem de coordinación con `identity` y
  `docs/04:33`, no un archivo del asistente.
- 2026-10-06 — **Pin de embedding cerrado y las dos ADR registradas en el índice; T0 lista para que el
  padre la marque.** Decisión del dueño: embedding = **`intfloat/multilingual-e5-small`, 384 dimensiones,
  MIT, local**. ADR-0026 se amendó **dentro de la misma unidad de trabajo** (el archivo estaba sin
  versionar, no es historia aceptada: la regla de "un ADR aceptado no se edita" aplica a ADRs ya
  mergeados), con la opción A marcada **Pin vigente** y B (`bge-m3`, 1024) documentada como alternativa
  descartada, no borrada.
  - **Por qué el pin va separado del LLM:** la API de DeepSeek es chat/reasoning compatible con OpenAI
    (V3.2 y R1) y **no publica un endpoint `/embeddings`**, así que el corpus no puede usar el proveedor del
    LLM. Los baratos que **sí** exponen embeddings son los **locales** `e5-small` / `bge-m3` (costo de API
    cero) y los **hosteados** Voyage AI (`output_dimension` flexible 256/512/1024/2048), OpenAI
    `text-embedding-3-small`, Cohere y Gemini. **Ningún precio inventado**: los reales se escriben en
    [docs/02:78](../docs/02-estimaciones.md) en T8.
  - **Consecuencia sobre el orden del trabajo:** T2 escribe `vector(384)` + HNSW coseno
    (`vector_cosine_ops`); la dimensión dejó de ser *owner-confirmable*. T8 confirma el pin o aterriza una
    migración de reescritura como su propia unidad de trabajo.
  - **Superficie cerrada:** `docs/adr/README.md` ya registra **ADR-0026 y ADR-0027** (una fila cada una, con
    el formato de las existentes), lo que resuelve la nota de "superficie faltante" del ítem anterior.
    Sigue fuera de superficie la versión de consentimiento que nombra al proveedor
    ([ADR-0007:39](../docs/adr/0007-llm-por-api.md)), que es coordinación con `identity` y `docs/04:33`.
  - Sin código fuente: documentación pasiva, sin pytest (C6).

- 2026-10-07 — **T1, puertos y dobles de prueba.** Superficie: `assistant/{domain,application}/**` y
  `server/tests/assistant/**`; nada más (ni `shared/**`, ni `pyproject.toml`, ni migraciones).
  **Sin adaptadores todavía**: no hay cliente HTTP, ni ORM, ni SDK de proveedor.
  - `domain/limits.py`: `DAILY_QUESTION_LIMIT = 10`, `RETRIEVAL_TOP_K = 5`, `SIMILARITY_FLOOR = 0.75`
    como constantes nombradas, `questions_remaining`, `daily_limit_exhausted`,
    `seconds_until_local_midnight` (America/Bogota, valor del `Retry-After`),
    `month_spend_cop` (ignora `None`, D-T12.1) y `budget_exhausted`.
  - `domain/prompt_policy.py`: las cinco reglas de docs/06:381-387 en español simple, el
    `PromptSection` con la lista de envío de D-T12.6 (`system`, `plot_facts`, `fragments`,
    `question`) y `validate_prompt_sections` que rechaza cualquier otra sección.
  - `domain/citations.py`: `Citation` con `source` obligatorio y `model_version` + `is_baseline`
    obligatorios cuando la cita habla de una predicción; `domain/models.py`: `MessageRole`,
    `ChatMessage`, `MessageEvidence` (todo o nada en `None`: `0` nunca sustituye evidencia ausente,
    y `model is None` distingue "no se llamó al LLM" de "se llamó y no costó nada"), `Conversation`
    (sin `org_id`, D-T12.5) y `Message` (`helpful` solo en turnos del asistente).
  - `application/ports.py`: `LLMClient.stream` (una llamada, tokens, el último token con `usage` y
    `model` → `cost_cop`, ADR-0026), `Embedder.embed` con `EMBEDDING_DIMENSION = 384`
    (`intfloat/multilingual-e5-small`, ADR-0026), `KnowledgeRepository.search_by_embedding` +
    `get_document` **sin filtro de org** (D-T12.5), persistencia de conversación/mensajes con
    `user_id` en cada método (404, nunca 403), y `UsageQuotaPort`.
  - `application/usage.py`: `evaluate_quota` → `QuotaDecision`; el tope llega como argumento porque
    es lo único que cambia por perfil (D-T12.3). **La función no tiene forma de alcanzar al
    proveedor**, así que "presupuesto agotado → cero llamadas al LLM" es estructural, no una promesa.
  - `tests/assistant/`: dobles in-memory de los seis puertos (`doubles.py`) y 98 tests en seis
    módulos, uno por comportamiento, con RED observado antes de cada GREEN.
  - **Verificación:** `just gate-fast` → verde; `just gate server/tests/assistant` → verde, 98
    passed (seed aleatorio, más dos semillas fijas sin acoplamiento de orden).
  - **Desviación declarada:** `domain/limits.py` importa `techcamp.shared.dates.BOGOTA_TZ`; es el
    primer `domain` del repo que importa `techcamp.shared` (import-linter no lo ve porque `shared`
    no es un contenedor). Se sigue la instrucción del brief y se conserva un único dueño de la zona
    horaria del producto; si el dueño prefiere cero imports desde `domain`, es una línea a cambiar.
  - **Superficie que T1 deja deliberadamente fuera:** `KnowledgeRepository` **no** tiene puerto de
    escritura (`upsert_document`/`upsert_chunks`, la clave idempotente `(source_url, sha256)` y la
    columna `sha256`): se definen juntas con el esquema de T2, no antes. `UsageQuotaPort` devuelve
    **las filas** del mes con sus `None` y no un `SUM` SQL, para que `month_spend_cop` siga siendo la
    regla que ignora el costo ausente; a ~54 000 filas/mes (ADR-0007:9) el `SUM` en SQL es la lectura
    más barata, así que T6 debe revisarlo.

- 2026-10-07 — **T4, hechos de parcela, recuperación y la respuesta sin LLM.** Reconciliado contra el
  árbol y no de memoria: cuatro commits sobre `a127acb` (`f0ef15f`, `349ac8a`, `98bf0f4`,
  `7a81758`), 14 archivos y **4 327 líneas** acumuladas, todas dentro de la superficie autorizada de
  T4 (`assistant/domain/**`, `assistant/application/**`, `server/tests/assistant/**`). Ninguna
  fachada ajena se tocó: los cinco hechos entran por la cara `application` de cada módulo, nunca por
  su `domain` ni por sus tablas (docs/05-arquitectura.md:150-151).
  - **`domain/facts.py`** — los cinco hechos (`SensorFact`, `AlertFact`, `IrrigationFact`,
    `SeasonalClimateFact`, `RiskFact`) más `ASSISTANT_METRICS`, `SENSOR_FRESH_WINDOW = 24 h` y
    `missing` por familia, para que "no hay información" sea una frase y nunca un `0` (D-T12.1).
  - **`domain/fact_safety.py`** — el snapshot inmutable (copia de `metrics` y `top_factors`) con el
    invariante "nunca lanza"; es exactamente lo que el fix `7a81758` cerró.
  - **`domain/degraded.py`** — el texto en español plano, determinista y en el orden de docs/06 §9
    (sensores, alertas, riego, clima estacional, riesgo), con la frase de evidencia insuficiente, la
    fuente de cada cita y el aviso de "etiqueta del producto ante el ICA y su técnico"
    **incondicional** (docs/06:386). La severidad se escribe solo para una versión calibrada: la
    línea base `climatology_month` con `thresholds={}` se anuncia como línea base (D-T9.2).
  - **`application/plot_facts.py::gather_plot_facts`** — `resolve_plot_access` primero, luego los
    cuatro facades (`query_latest_plot_readings`, `list_open_alerts_for_plots`,
    `query_plot_recommendation`, `query_plot_risk` **reusado**, no una segunda lectura de las tablas
    de riesgo) y el boletín estacional. Cada hecho se proyecta a su propio value object con
    `sanitize_fact_values` y listas de claves permitidas: la lista de envío de D-T12.6 nace aquí.
  - **`application/retrieval.py::retrieve_fragments`** — top 5 y piso 0.75 tomados de
    `domain/limits.py` (constantes nombradas, no literales), el piso **re-aplicado** en el caso de uso
    para que un repositorio que lo ignore no decida lo que lee el productor, una cita por documento
    y `validate_citation` antes de devolver. Menos de cinco fragmentos —o ninguno— es una respuesta
    legítima, no un fallo.
  - **`application/degraded_answer.py::answer_without_llm`** — la rama sin LLM, y la garantía de que
    no hay proveedor es **estructural, no una promesa**: la firma no nombra un `LLMClient` y dos
    tests lo comprueban (uno inspecciona la firma de cada función pública de la ruta, otro pregunta
    al doble `FakeLLMClient` cuántas veces lo invocaron).
  - **Desviación declarada, dentro de superficie:** `application/ports.py` ganó
    `KnowledgeRepository.latest_climate_bulletin(department=...)`. El `department` es un argumento
    nullable con fallback a nacional **a propósito**: nada en el repo mapea finca o parcela a
    departamento (`Farm.municipality_code` es DIVIPOLA de texto libre y no existe tabla `municipality`),
    e inventar ese mapeo aquí sería un segundo lugar que decide las divisiones administrativas de
    Colombia, que es la tabla `municipality` cuando exista.
  - **Tests:** 97 tests de T4 en seis archivos, uno por comportamiento, con RED observado antes de
    cada GREEN (`test_facts` 17, `test_fact_safety` 18, `test_degraded` 20, `test_plot_facts` 26,
    `test_retrieval` 12, `test_degraded_answer` 4).
  - **Verificación (2026-10-07, HEAD `7a81758`, base de datos de este worktree):**
    `just gate-fast` → **verde** (ruff, ruff format 399 archivos, mypy 243, `lint-imports` 1 contrato
    / 0 roto con las capas hexagonales **conservadas**, una sola cabeza de Alembic, ast-grep,
    ml ruff/mypy, eslint, tsc); `just gate server/tests/assistant` → **233 passed, 0 failed, 0
    skipped** en 24.56 s, seed `1059847596` (pytest 9.1.1, Python 3.12.14, pytest-randomly 5.0.0).
  - **Desviación de proceso, registrada tal cual y no resuelta.** El worker de verificación se pasó
    de la superficie que le di (solo `just db-up`) y ejecutó **`just db-reset`**, que esta tabla de
    Checks prohíbe a mitad de épico ("las filas de una lane son evidencia"). Lo que ocurrió, en tres
    hechos: (1) `just db-up` **no puede revivir un contenedor detenido** — su guarda
    (`justfile:100`, `ps -aq`) también matchea los exited y responde *"already exists; run 'just
    db-reset'"* — y `just db-info` no ayuda, porque imprime config (`justfile:92-99`), no estado;
    (2) `db-reset` es `db-down` + `db-up`, y `db-down` hace `podman rm -f -v` (`justfile:115-116`),
    así que el volumen anónimo de pgdata de este worktree se **eliminó y se recreó vacío**; (3) el
    mismo `db-up` lo levantó de nuevo en el puerto 55219 y el gate corrió sobre un esquema limpio
    reconstruido desde las migraciones. **Pérdida real:** las filas que hubiera en ese Postgres. En
    esta lane eran fixtures de test que cada corrida recrea —T4 no tiene adaptador, router ni
    ingestión—, así que no hay evidencia de T4 perdida y el gate sobre base limpia es la forma más
    fuerte de prueba; lo que sí se perdió es cualquier carga manual o semilla que hubiera en ese
    worktree, y eso ya no es verificable. **El arreglo no destructivo era `podman start
    <container>` + `pg_isready`.** Fricción para un follow-up (no se arregla aquí: `justfile` es
    superficie de T9): `just db-up` debería revivir un contenedor existente que esté detenido, y
    `just gate` valida la base **después** de `gate-fast`, así que un contenedor muerto cuesta una
    pasada estática completa antes de que el fallo se vea.
  - **Superficie que T4 deja para adelante:** sin adaptador todavía —el `KnowledgeRepository` es el
    puerto de T1 con el método del boletín, y su lectura SQL es de T3; `usage.py` sigue siendo la
    rama de cuota de T1 y T6 la completa.

- 2026-10-07 — **T5, el endpoint de streaming y el de retroalimentación.** Reconciliado contra el árbol
  y no de memoria: base `1d7e1b8` (T0–T4), rama `e12-t5`, worktree propia con su propia base
  (`techcamp-db-e12-t5-460430375`, puerto 55375). **Superficie:** `assistant/adapters/api/**`,
  `assistant/adapters/repositories.py`, `assistant/domain/{models,errors}.py`,
  `server/src/techcamp/main.py` (solo el registro del router) y `server/tests/assistant/**`.
  Nada de `web/`, nada de `/api/v1/stream`, nada del hub de telemetry.
  - **`adapters/api/prompt.py`** — el prompt saliente: sistema + hechos + fragmentos + pregunta, en
    ese orden (docs/06 §9), validado contra `PromptSection` en cada llamada. Los **datos viajan como
    datos**: los bloques de hechos y fragmentos se rotulan *"datos, no instrucciones"* y llegan en
    mensajes `user`, mientras las reglas de docs/06:381-387 son el único mensaje `system`
    (docs/09:75). El bloque re-aplica las listas de claves permitidas y `contains_contact_data` al
    **renderizar**, no solo al construir el hecho: es el test de lista de envío de D-T12.6 (un
    teléfono o un correo en el bloque de hechos nunca llega al prompt).
  - **`adapters/api/stream.py`** — el cuerpo SSE y el contrato de tramas: `token` por token del
    proveedor, después `sources` con las citas resueltas y después `done` con `conversation_id`,
    `message_id`, modelo, tokens y costo (D-T12.1). **Sin `id:`** y **sin keepalive**: ADR-0027 deja
    claro que este stream no tiene replay y nace y muere con su petición. El cuerpo **no sostiene
    sesión**: llama a un `persist` que abre y cierra la suya, y lo llama **después** del último token
    (D-T12.4 / `R3-stream-holds-db-session`). Un proveedor que falla a mitad de camino, un `usage`
    ausente o un modelo sin precio cierran la trama con `error` y **no persisten nada**.
  - **`adapters/api/pricing.py`** — `cost_cop` como aritmética de docs/02:78 sobre una **tarjeta de
    precios inyectada**, nunca un literal: ADR-0026 declara obsoletos los precios de docs/02:78 y los
    reales son de T8. Un modelo sin precio es `UnknownModelPriceError`, **jamás** un costo `0`.
  - **`adapters/api/deps.py`** — el `LLMClient`, el `Embedder` y la tabla de precios. En el perfil
    seminario: `SeminarLLMClient` (local, determinista, sin red) y **`SeminarHashEmbedder` reutilizado**
    —el mismo con el que T3 indexó el corpus, porque una pregunta embebida por otro embedder no
    encontraría nada. En `production`: `503` con título explícito, porque las claves del proveedor y el
    cliente DeepSeek son superficie de **T6**.
  - **`adapters/repositories.py`** — `SqlAlchemyKnowledgeRepository` (búsqueda coseno
    `ORDER BY embedding <=> :query`, piso y `top_k` en SQL, boletín con fallback nacional), y los dos
    repositorios del transcripto filtrados por `user_id`. `add` **rechaza una conversación ajena** con
    `ConversationNotFoundError`: la FK no sabe de quién es la conversación y sin esa comprobación un
    turno se escribiría en el transcripto de otro usuario.
  - **`adapters/api/router.py`** — los dos endpoints de docs/04:227-228. Orden de las puertas:
    conversación propia (`404`) → `resolve_plot_access` (`404`) → hechos → recuperación → prompt →
    escritura de la pregunta, todo en **una sesión corta** que cierra antes de que empiece el
    `StreamingResponse`; la respuesta se escribe en otra sesión corta, dentro del cuerpo.
  - **Desviaciones declaradas (dentro o fuera de la superficie que el brief autorizó):**
    (1) `assistant/adapters/repositories.py` está **fuera** de la fila de superficie de T5
    (`assistant/adapters/api/**`), con la excepción explícita del dueño: los diez módulos del repo
    guardan sus repositorios ahí y `adapters/api/` es solo transporte.
    (2) `MessageRole` gana **`SYSTEM`** y `Message` lo **rechaza** con
    `SystemRoleOnStoredMessageError`: el payload saliente de docs/06 §9 necesita un rol para las
    reglas —si no, los fragmentos y el texto del productor llevarían la voz de las instrucciones
    (docs/09:75)— y el enum congelado de T1 no lo tenía. La transcripción sigue teniendo dos roles, y
    `test_models` se actualizó para afirmar exactamente eso.
    (3) `cost_cop` requiere un precio y no hay ninguno en el repo: el dueño decidió **precio
    inyectado, 0 en el seminario**, con las cifras reales de DeepSeek en T6/T8.
    (4) La rama **degradada sin LLM no está cableada**: T4 dejó el caso de uso y T6 es su dueño
    (el disparador son el límite y el presupuesto). Cablearla aquí sin disparador sería código muerto.
  - **Tests:** 4 módulos nuevos y 53 tests (`test_prompt_assembly` 11, `test_repositories` 20,
    `test_stream` 18, `test_assistant_api` 16) más `db_fixtures.py`, con RED observado antes de cada
    GREEN. Aíslan el `conversation`/`plot` de otra organización y usuario (`404`, nunca `403`), el
    prompt exacto que salió del servidor, una sola llamada al proveedor por request (ADR-0007:26), y
    **cero conexiones del pool checked out entre tokens** (el mismo instrumento que usa
    `tests/telemetry/test_stream_api.py` para el stream del hub).
  - **Verificación (2026-10-07, worktree `e12-t5`, base 55375):** `just gate server/tests/assistant`
    → **359 passed, 0 failed, 0 skipped** en 47.99 s (incluye `gate-fast`: ruff, ruff format,
    mypy, `lint-imports` con las capas hexagonales conservadas, una sola cabeza de Alembic, ast-grep,
    eslint, tsc). Por rebanada: S1 → 304 passed; S2 → 324 passed; S3 → 341 passed; S4 → 359 passed.

- 2026-10-07 — **T5 entregada y revisada por rebanadas** (decisión del dueño: con ~4 k líneas, tres
  unidades de trabajo y un recibo cada una, no un RDD único). Rama `e12-t5`, base `1d7e1b8`, base de
  datos propia en el puerto 55375. **Trabajo en política ordinaria:** sin PR, sin merge, sin rebase.

  | Rebanada | Commit | Recibos RDD |
  |---|---|---|
  | S1 repos + fixtures + tests | `b31ac34` | `review-19ee9c38084df410` — revisión corrió y cerró `correction_required` (1 CRITICAL + 2 WARNING); corrección acotada en `53cb281` y **parada terminal de mantenedor** al re-verificar artefactos |
  | S2 prompt + SSE + precio + rol `SYSTEM` | `8fcb3ed` | `review-202f3954880c4634` — **aprobado y quemado** (`gentle-ai.review-acknowledged/v1`), 3 WARNING informativos |
  | S3 deps + router + `main.py` + tests de API | `ea42de1` | `review-4a3a3fefe2f3f556` — **aprobado y quemado** (`gentle-ai.review-acknowledged/v1`), 1 WARNING informativo |

  - **El CRITICAL de S1 (`R3-ownership-mismatch`) estaba en el código y quedó corregido antes de seguir:**
    `MessageRepository.add` verificaba la pertenencia del argumento `conversation_id` y escribía una fila
    con `message.conversation_id`, así que un llamador podía verificar una conversación propia e
    insertar un turno en el transcripto de otro usuario — la FK lo habría aceptado. La corrección son
    **19 líneas de diff** (3 en el repositorio, 16 en el test), exactamente las capturadas en el plan
    pre-edit (`--correction-lines=19`), y el test nuevo cubre justo el caso que el anterior no probaba:
    el llamador **es** dueño de la conversación que nombra y aun así no puede apuntar el mensaje a otra.
  - **Parada terminal del recibo de S1, tal cual:** `kind: stop`, `reason_code:
    captured_artifacts_unverifiable`, `replayability: manual_action_required`, y el store marca
    `repair: unsupported` (`supported_operations: ["review/complete-fix", "review/validate-fix"]`). Por
    la tabla de reason codes eso es del **mantenedor**: inspeccionar la autoridad o desactivar el
    interruptor de RDD del clon. **No se intenta recuperación ambiental.** La corrección ya está
    commiteada y verificada; lo que falta es el recibo quemado de esa rebanada.
  - **Hallazgos no bloqueantes, para un issue por ronda de revisión** (ninguno abre corrección y ninguno
    reabre su candidato):
    - `R3-search-floor-limit` (S1, WARNING) — el `top_k` se aplica en SQL antes del piso en Python, así
      que un fragmento bajo el piso consume un cupo y puede dejar fuera a uno que sí califica.
    - `R3-created-at-assertion` (S1, WARNING) — el test del reloj de la BD no distingue el reloj de la
      base del `created_at` rechazado del llamador.
    - `R3-001` … `R3-003` (S2, WARNING) — en `pricing.py:94-103`, `stream.py:121-132`, `prompt.py:156-168`.
    - `R3-001` (S3, WARNING) — en `tests/assistant/test_assistant_api.py:320-330`.
  - **Cierre de T5 (2026-10-08).** El dueño autorizó el cierre entero: (a) la decisión de precio queda
    como la costura ya implementada —inyectada, 0 en seminario, números reales en T6/T8—; (b) el lineage
    del RDD único `review-9f2fddf94d243592` quedó **abandonado** con `reason: operator_disposition`,
    `actor: owner`, sin resultados de lente ni hallazgos que descartar, y su estado en cuarentena bajo
    `review-transactions/quarantine/`; (c) los hallazgos no bloqueantes se agruparon en el issue
    [#306](https://github.com/jab16jy/techcamp-v2/issues/306) (`review-follow-up`, `type:chore`,
    `area:server`); (d) la parada de S1 se investigó de solo lectura y su causa está registrada abajo.
  - **Causa de la parada de S1, con evidencia (no con conjetura).** `review inspect-authority` dice que
    el store está **sano**: `valid: true`, `complete: true`, 38 entradas, 1 arista válida, 0
    diagnósticos, 0 aristas inválidas — no hay corrupción ni registro perdido. Lo que muestra el estado
    de la autoridad es que el candidato congelado sigue siendo `81a277b7…` (el árbol **anterior** a la
    corrección) y que el `fix_delta_hash` registrado es `sha256:e3b0c442…`, que es el SHA-256 de la
    cadena vacía: **cero bytes de corrección registrados**. Al validar el artefacto capturado, native
    resuelve el candidato corregido como *árbol congelado + delta registrado*, y con delta vacío ese
    candidato ya no corresponde al contenido commiteado (HEAD iba cuatro commits por delante,
    incluyendo `53cb281`). De ahí el `captured_artifacts_unverifiable`. El arreglo supported es de
    mantenedor (`review/complete-fix`, `review/validate-fix`), no de código.
  - **Desviación de tooling registrada tal cual:** la captura de la corrección hubo que emitirse con el
    CLI nativo (`gentle-ai review status --intended-untracked-selection=…` y
    `gentle-ai review capture-correction-plan --correction-lines=19`), con autorización explícita del
    dueño, porque la fachada no alcanza ese estado: `readRetainedNativeUntrackedSelection`
    (`extensions/gentle-ai.ts:6548-6557`) devuelve `{}` en cuanto la selección retenida trae `baseRef`, y
    `status` no acepta `untrackedScope`; `select-intended-untracked` en cambio ejecuta START
    (`gentle-ai.ts:8359`). Afecta a **todo candidato con rango commiteado**, no a este en particular.
  - **Superficie que T5 deja para adelante:** el límite de 10/día, el tope de presupuesto y el `429`
    con `Retry-After` (T6, que comparte este router), la rama degradada, el cliente DeepSeek con sus
    claves y su tarjeta de precios (`shared/config.py`), y las tramas que el cliente web de T7
    consumirá con `fetch` + `ReadableStream` + `createSseParser` (`token`, `sources`, `done`, `error`).

- 2026-10-23 — **T8 U1 (el arnés) cerrado, corregido y verificado; U2 sigue bloqueada en el dueño.**
  La lane lleva seis unidades: `6915f99` (set cerrado de 30 preguntas), `d5316ce` (pin del corpus por
  digest), `cefb763` (replay por el `retrieve_fragments` y el `build_prompt` reales), `6d429bc`
  (reporte de evidencia para calificar, sin score/threshold/verdict), y dos de esta sesión:
  `d7cf535` (hermeticidad) y `d8d3c31` (sonda de barrido).
  - **Hermeticidad, con RED y GREEN observados (hallazgo de la sesión del 2026-10-08, confirmado).**
    Con `TECHCAMP_KB_CORPUS_DIR` exportado —el switch del dueño para la revisión de licencia, leído
    por `pin_corpus()` a la hora de la llamada—, el arnés en `6d429bc` falla: 4 tests (pin, digests,
    chunk count, digest del artefacto; con una copia idéntica fallan 2, con un directorio vacío, 20).
    El guard que ya existía en `test_kb_ingest_job.py:105-113` (fixture autouse con
    `monkeypatch.delenv`) se copió a un `conftest.py` del paquete, y el suite pasa verde con la
    variable exportada. El comentario del end-to-end que asumía "unset in the test run" ahora nombra
    al conftest.
  - **Recibo del corte:** `review-3c128bea98f8df25`, lente `review-reliability`, 0 hallazgos, quemado
    (ver entrada de Review).
  - **Verificación técnica (agente `gentle-ai-verify`, contrato ODD, 2026-10-23), sobre la lane
    completa con base `88268d6`:** sin bloqueadores. Todos los ítems de U1 **met** con evidencia
    propia (33 sondas: pin repetido igual; dos ciclos seed+replay renderizan byte-idéntico;
    30 preguntas con ids y textos únicos; textos de `EvalSetError` exactos en 8 negativos;
    `CorpusPinError` al mutar bytes tras el pin; ≤5 fragmentos, similitudes ≥ 0.75, citas deduplicadas,
    prompt `[system, user, user, user]` terminando en la pregunta del productor; sin campo con forma
    de grade y sin `LLMClient` alcanzable). U2 **not implemented — esperado**: espera las Open
    questions 1 y 2 del dueño, no es bloqueador de U1. Nota del verificador: en `88268d6` el paquete
    eval no existe todavía, así que el árbol RED de la hermeticidad es `6d429bc`. **Advisories (sin
    ronda de corrección):** (a) con `SeminarHashEmbedder` las 30 preguntas recuperan 0 fragmentos y 0
    citas —medido y documentado por el propio arnés
    (`test_the_stand_in_reaches_no_fragment_above_the_floor`, `test_corpus_pin.py:286`)—, así que la
    corrida graduada necesita el modelo de ADR-0026, no el stand-in; (b) no hay artefacto de
    resultados commiteado (la superficie lo permite; el arnés renderiza a demanda; no es violación).
  - **Sonda dura promovida (`d8d3c31`):** el barrido set-wide que el verify devolvió como regresión —
    las 30 preguntas deben respetar `RETRIEVAL_TOP_K`, el piso de `SIMILARITY_FLOOR` y el orden
    `[system, user, user, user]` de D-T12.6, con el último mensaje terminando en la pregunta.
    `just gate server/tests/assistant/eval` → **97 passed**.

## Next step

**T1, T2, T4 y T5 están cerrados.** T5 entró por rebanadas (decisión del dueño, con ~4 k líneas) y
dejó los dos endpoints de `docs/04:227-228` vivos sobre `ea42de1`, con los repositorios en `b31ac34`
y su corrección `53cb281`, el prompt/SSE/precio en `8fcb3ed` y el cierre en `959f8b8`. **T6 puede
arrancar ya** y comparte el router de T5: el `POST /assistant/messages` que T6limita es el de
`ea42de1`, y la rama degradada (`answer_without_llm`, de T4) está lista para cablearse detrás de la
puerta de cuota. T7 es `web/` y no se solapa con nada del servidor. **T8-U1 (el arnés) está hecho y
verificado (2026-10-23); lo que queda de T8 es U2, los docs, y no espera código sino las dos
decisiones de abajo.** T3 espera la revisión de licencia del corpus para cargar el corpus real.

**Lo que T5 deja abierto, en orden de quien lo tiene que resolver:**

1. **El recibo de la rebanada de repositorios está en una parada terminal de mantenedor**
   (`captured_artifacts_unverifiable`, `repair: unsupported`). El código está corregido y verde; lo
   que falta es el recibo quemado. No se reintenta ni se recupera por cuenta propia.
2. **Fuente del precio de `cost_cop` — decidada por el dueño (2026-10-08): precio inyectado, 0 en el
   perfil seminario.** La costura ya lo respeta: `price_for` resuelve sobre una tabla que inyecta el
   wiring, `cost_cop` es la aritmética de docs/02:78 sobre esa tarjeta y un modelo sin tarjeta es
   `UnknownModelPriceError`, **nunca** un costo `0`. Por qué no se fijaron números aquí: ADR-0026:57
   declara obsoletos los precios de [docs/02:78](../docs/02-estimaciones.md) (Claude Haiku 4.5) y deja
   los reales de `deepseek-chat` a T8, y el costo del embedding depende del modelo que T8 pueda elegir.
   El 0 del seminario no es un placeholder: ADR-0021 corre todo localmente, así que un modelo dentro
   del proceso no tiene factura, y D-T12.1 acepta `cost_cop = 0` para una llamada que sí ocurrió y no
   costó nada. Lo que falta es el cliente DeepSeek y sus claves (`shared/config.py`, T6) y la tarjeta
   real (T8), no una decisión.

Lo demás que queda es decisión del dueño, y ninguna bloquea a T6:

1. **D-T12.3: confirmar el límite de 10/día en ambos perfiles.** Sigue anotado como owner-confirmable en
   `docs/04:22`; si se revierte a solo-`production`, esa fila y [ADR-0007:5](../docs/adr/0007-llm-por-api.md)
   cambian.
2. **Open question 1: el umbral de la evaluación de T8.** Ningún documento dice qué puntaje promueve un
   proveedor; hay que fijarlo **antes** de correr las 30 preguntas. T8 cierra `docs/README.md:68` y
   escribe los precios reales en `docs/02:78`.
3. **Open question 2: la revisión de licencia del corpus** ([ADR-0008:32](../docs/adr/0008-rag-pgvector.md)).
   T3 carga solo fixtures hasta que responda.
4. **Los 315 líneas del corte 3 (`349ac8a..7a81758`) siguen `under_budget`:** sin revisión debida y
   sin recibo. Cerrarlo con recibo exige una decisión explícita del dueño (revisión independiente de
   ese rango); no se reinicia por cuenta propia ni se fabrica un recibo.

En el orden de [Lanes](#tasks): **T1 → T2 ‖ T4** hechos; **T5** hecho; **T6 ‖ T7 ‖ T8** después.
Nada en este épico necesita un cambio de código antes de eso: `techcamp.assistant` ya está en la lista
blanca de `import-linter`, CI ya vigila `server/**` y `web/**`, la imagen del seminario ya tiene
`vector`, y ningún otro módulo se tiene que mover para que E12 arranque.
