# TechCamp v2 — E12 Blockers Assessment (pre-implementation)

## Objective

Assess what blocks the development of epic **E12 "Asistente"** from `docs/10-dag.md:75` ("Asistente con
fuentes citadas, límites y degradación"; deps E6, E7, E10) **before** any E12 source code is written, and
fix the decisions that would otherwise force rework mid-epic.

This document is the pre-E12 assessment. The E12 feature doc (`odd/tasks/techcamp-v2-e12-assistant.md`) is
itself a task (T0 below) and is the gate AGENTS.md imposes before code.

Sources: docs sweep (docs are the source of truth, AGENTS.md rule 1), repo-state sweep, live issue-tracker
sweep (129 issues, all states), library verification through `find-docs` / Context7.
RDD: on (`gentle-ai review mode status` → global on). No source file changed by this assessment, so no
review candidate was opened.

## Verdict

**E12 can start. No GitHub issue blocks it.** Every real blocker is an unfiled gap in the repo or an
undecided item in the docs. Six decisions (D-T12.1 … D-T12.6) are cheap to take now and expensive to
take after the schema or the SSE endpoint exists.

## Blockers — resolve before or with the first code commit

| # | Blocker | Evidence | Smallest unblocking action |
|---|---|---|---|
| B1 | No E12 feature doc; `odd/tasks/` has none for E12 | `odd/tasks/` listing; AGENTS.md workflow | Create `techcamp-v2-e12-assistant.md` (T0) |
| B2 | The USD-5 cap has nowhere to live: §9 requires persisting cost per answer, `message` stores tokens + `helpful` and no cost | `docs/06-diseno-detallado.md:365`, `docs/03-modelo-datos.md:363-374`, `docs/adr/0007-llm-por-api.md:17`, `docs/11-metricas.md:132` | Decide cost state (column vs ledger table) in docs before T5 |
| B3 | No schema for the assistant: no `kb_document`/`kb_chunk`/`conversation`/`message` migration, no `vector` extension in any migration, and the `pgvector` Python package is not a dependency | grep over `server/migrations/versions/` = 0 hits; only `postgis` (`9098dc0927a3:26`) and `timescaledb` (`8c3983dc2dfd:79`) are created; `infra/postgres/init-extensions.sql:6` installs `vector` at image init; `server/pyproject.toml:11-30` has no pgvector | One revision chaining onto head `b6e1c4a7f2d9` (T2); `justfile:140-147` fails on a second head |
| B4 | No rate limit or usage port exists anywhere; the 10-questions/day cap + `429 Retry-After` is net-new `shared/` work | `docs/04-api.md:22`; grep `rate|429|Retry-After` over `server/src` finds only the Open-Meteo handler | Size it as its own task (T6), not a reuse |
| B5 | Provider undecided (30-question eval pending): Haiku 4.5 vs Flash | `docs/README.md:68`, `docs/adr/0007-llm-por-api.md:15` | Pin a provisional provider with an ADR note, or run the eval first; ADR-0007:14 already fixes the provider-agnostic port `stream(messages) → tokens`, which keeps the blast radius small |
| B6 | Daily-limit scope conflict: always 10/day vs only in production | `docs/04-api.md:22` vs `docs/adr/0007-llm-por-api.md:5` and `docs/adr/0021-perfil-seminario-local.md:30` | One owner decision; the seminar profile answers it, the docs must agree |

## Degraded — E12 proceeds, with a documented limitation

| # | Degradation | Evidence | Handling |
|---|---|---|---|
| D1 | Every served risk is `low`: the baseline version carries `thresholds={}` | `risk/adapters/climatology.py:33-35`, `risk/domain/models.py:145-166` | The assistant must cite `model_version`, mark the baseline, and never narrate "low" as a verdict. Issue #277 (screen absent) is open |
| D2 | `drought` is an enum value with no registered predictor (only `risk_flood`) | `risk/domain/models.py:36-37,52-53`, `risk/adapters/registry.py:29` | The risk fact degrades to "not available" instead of inventing a value; `flood_risk`/`drought_risk` are DI names, not API codes |
| D3 | `risk` repositories carry no `org_id` (keyed by `weather_cell`); the assistant must still isolate by org | `risk/adapters/repositories.py:5-8` vs the pattern `alerts/adapters/repositories.py:3,108` | Follow the alerts pattern; decide scoping of the cross-org weather/risk facts before T4 |
| D4 | The E6 rationale is reachable in-process only through the `home` composition module; E12 would be its first consumer outside `home` | `irrigation/domain/models.py:456`, `irrigation/adapters/api/router.py:55-58`, `home/application/plot_status.py:40-51` | A real use case plus port, not an import shortcut (hexagonal rule) |
| D5 | No ingestion path for the ENSO bulletin corpus; no retention/deletion rule for `conversation`/`message` | `docs/06-diseno-detallado.md:374`, `docs/adr/0008-rag-pgvector.md:32`, `docs/03-modelo-datos.md:363-374` | Define the loader (procrastinate job, ADR-0012) and the retention rule before T3 |

## Contract conflicts to settle in docs (docs win, AGENTS.md rule 2)

| # | Conflict | Line A | Line B |
|---|---|---|---|
| C1 | Daily limit always vs production only | `docs/04-api.md:22` | `docs/adr/0007-llm-por-api.md:5`, `docs/adr/0021-perfil-seminario-local.md:30` |
| C2 | SSE direction: ADR-0015 defines SSE as server→client on `GET /api/v1/stream` | `docs/adr/0015-tiempo-real-sse.md:6,10` | `docs/04-api.md:227` (POST returning `text/event-stream`) |
| C3 | "1 stream per user" covers a stream the API contract does not describe | `docs/09-cuellos-de-botella.md:28` | `docs/04-api.md:227` |
| C4 | Reference model fixed by cost (Haiku 4.5, USD 1/5) while the provider is pending | `docs/02-estimaciones.md:78` | `docs/adr/0007-llm-por-api.md:14-15` |
| C5 | The kb corpus has no `org_id`, against the rule that every repository filters by `org_id` | `docs/03-modelo-datos.md:347-362` | `AGENTS.md:91`, `docs/09-cuellos-de-botella.md:72` |
| C6 | TDD per behavior change vs "one bounded run at the end" | `AGENTS.md:100` | Owner instruction for this assessment |

Resolution for C6: this assessment writes no source and therefore runs **no** pytest. For E12 itself the
repo rule stands — RED → GREEN → REFACTOR with tests next to the behavior, one `just gate <paths…>` per
work unit, `gate-full` only at epic close. Owner decision required if a cheaper cadence is really wanted.

## Already in place — reuse, do not rebuild

| Asset | Evidence |
|---|---|
| `techcamp.assistant` already whitelisted in the import-linter contract | `server/pyproject.toml:87-109` (containers list, line 109) |
| `vector` extension present in the seminar image, verified live in E0 (pgvector 0.8.6) | `infra/postgres/init-extensions.sql:6`, `odd/tasks/techcamp-v2-e0-foundations.md:44,69` |
| SSE transport pattern to copy | `telemetry/adapters/api/router.py:451-485` (`StreamingResponse` + `text/event-stream`) |
| Current user + org resolution for the new endpoint | `identity/adapters/api/deps.py:31-49` |
| Port → ORM → repository → migration triple to mirror | `risk/application/ports.py:65` → `risk/adapters/orm.py:71` → `risk/adapters/repositories.py:40-72` → `b6e1c4a7f2d9` |
| CI path filters already cover `server/**`; no assistant entry needed | `.github/workflows/ci.yml:45` |
| Web session plumbing a new screen consumes | `web/src/lib/api/session.ts:5-9` |
| Design system already reserves the assistant screen path | `docs/07-frontend-design-system.md:70,129` |

## Library facts verified for the E12 stack (Context7, 2026-10-06)

- **pgvector-python**: `pgvector.sqlalchemy.Vector(dim)` and `HALFVEC(dim)`; HNSW through
  `Index(..., postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})`; the
  half-precision variant uses a cast to `HALFVEC` with `halfvec_ops`. One decision needed: full `vector`
  (as `docs/03-modelo-datos.md:558` specifies) or `halfvec` to halve the ~50 MB corpus.
- **pgvector server**: `SET maintenance_work_mem = '8GB'` before a bulk build, `hnsw.iterative_scan =
  strict_order` when the query filters, partial HNSW (`WHERE category_id = …`) when filtering by a few
  distinct values, and `pg_stat_progress_create_index` to watch the build. The corpus is small
  (~10k fragments, `docs/02-estimaciones.md:90`), so a single build is fine; the open question is
  index-before-load vs load-then-index during ingestion.

## Issue-tracker sweep (live, `gh issue list --state all --limit 300`)

129 issues, 63 keyword hits over titles and bodies, **0 blockers**. One degradation (#277) and two
infrastructure notes:

| Issue | State | Bucket | Note |
|---|---|---|---|
| #277 | OPEN | DEGRADED | E10 risk screen absent (D-T9.2); E12 cites a weak risk signal |
| #5 | OPEN | NOISE for E12 | Its pgvector item is "automate the Postgres extension check, currently manual" — infra ergonomics, extension already verified live |
| #292, #293-#299, #259 | OPEN | NOISE | E10/E11 review follow-ups already merged or unrelated |
| E6/E7/E8/E9/E11 follow-ups (#33-#258 range) | mixed | NOISE | Per-epic RDD follow-ups; none touches the assistant |

## Proposed E12 task plan (needs owner authorization)

| Task | Content | Depends on |
|---|---|---|
| T0 | Create `odd/tasks/techcamp-v2-e12-assistant.md` with the requirement list and these decisions | — |
| T1 | Ports `LLMClient`, `Embedder`, `KnowledgeRepository` + test doubles; usage/quota port | T0, D-T12.1 |
| T2 | Migration: `CREATE EXTENSION vector`, `kb_document`, `kb_chunk` + HNSW cosine, `conversation`, `message`, cost column | T0, B2, B3 |
| T3 | Corpus ingestion: chunking (~500 tokens, overlap), source + licence per document, loader job | T2, D5 |
| T4 | Fact builders (sensor, alert, irrigation, ENSO, risk) + the no-LLM fallback path | T1, T2, D3, D4 |
| T5 | `POST /assistant/messages` (SSE) + `:feedback`; prompt policy and Spanish copy from the glossary | T4, C2, C3 |
| T6 | 10/day limit with `429 Retry-After`, USD 5 budget ledger, `llm_cost_cop_total` metric | T5, B4, C1 |
| T7 | `web/src/features/assistant/` screen, design-system tokens only | T5 |
| T8 | 30-question Caribbean eval, graded by an agronomist; provider decision ADR | T5, B5 |
| T9 | Gate + evidence, `gate-full` once, epic close | T1-T8 |

Each task closes with one `just gate <paths…>` run and one RDD receipt per work-unit commit, chained PRs
in order (AGENTS.md, `work-unit-commits`, `chained-pr`).

## Hard requirements that shape every task (docs sweep, condensed)

Ports `LLMClient`/`Embedder` only, deterministic single-call flow, no agent framework and no Ollama
(ADR-0007:13-14,26); facts assembled before the one call and cited by the LLM (`docs/06:369-375`); top-5
fragments with similarity ≥ 0.75 (ADR-0008:12); source + licence per document (ADR-0008:12); degrade to a
fact-only answer with no LLM (`docs/06:377`); never invent doses, refer to the ICA label and the technician
(`docs/06:386`); the LLM explains, it never computes irrigation, risk or doses (ADR-0007:16); RAG chunks and
user text travel as data with no writing tools (`docs/09:75`); minimum data to the provider, no personal data
(ADR-0007:39); org isolation with `404` for another org's resource (`docs/04:17`); reply in plain Spanish with
glossary terms (`docs/06:383`, `docs/00-glosario.md`).

## Undecided in the docs (owner input needed)

1. Provider and embedding model (B5) — the 30-question eval decides it, and no pass threshold is written.
2. Hybrid BM25 + vector search, only if the eval fails (ADR-0008:19).
3. Corpus licence before migration (ADR-0008:32).
4. Retention and deletion of `conversation` / `message`.
5. Whether `/assistant/messages` needs an explicit membership check beyond `CurrentUserId`.

## Session tooling (harness, not repo)

Subagents run on `opencode/space-bunny-free`: `thinking: xhigh` for explore/verify and the four review
lenses, `max` for the writer. The routing pin lives in `~/.pi/gentle-ai/models.json` (materialized) and is
mirrored in `~/.pi/agent/subagents.json`; the previous `opencode-free/...` provider did not resolve and had
been silently restored once, so re-check it if a launch fails with "Model not found".