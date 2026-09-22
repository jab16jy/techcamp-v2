# TechCamp v2 — Seminar alignment of the system design

## Objective
Make every design document consistent with ADR-0021 (seminar profile by default, production only if the project grows), and complete the assistant design so the low-cost LLM explains IoT sensor state and ML model outputs.

## Problem / why
- An audit against ADR-0021 found ADRs and metrics that still present production infrastructure (Supabase Auth, SMS provider, VPS/Caddy, paid S3, LoRaWAN hardware, SLOs, Prometheus/Grafana) as the current default.
- The assistant (06 §9, 05 module graph) receives facts from farms, irrigation, alerts and risk, but not from telemetry, and does not pass ML model quality or version. The user's intent is that the LLM is grounded in sensor readings and ML results.
- v1 used LangGraph (`TechCamp/backend/app/agent/graph.py`: linear START → orchestrator → generate → END). v2 dropped it without recording why.

## Scope
- In: scope notes pointing to ADR-0021 in ADRs 0004, 0007, 0014, 0015, 0016, 0017, 0018; ADR index scope column/note; ADR-0021 list of production-only ADRs includes 0018; 11-metricas §4 and its diagram scoped to production; 04-api `/metrics` and 01-requisitos Grafana item scoped; assistant context extended with telemetry and ML outputs in 05 and 06 §9; ADR-0007 alternatives row for LangGraph / agent frameworks.
- Out: any source code; changes to domain decisions; rewriting production content (it stays, marked as future).

## Constraints
- Docs in neutral professional Spanish; code identifiers in English. Mermaid diagrams.
- Minimal edits: add scope notes, do not delete production content.
- Module graph rules (05) must stay acyclic: `assistant --> telemetry` must not create a cycle.

## Route and checks
- TDD: off (docs only, no test runner; source: previous feature doc convention).
- Route: delegated direct. Writer trigger fired (10+ files); one Sonnet writer (user request: research/worker agents on Sonnet).
- Checks: internal link/anchor check (0 broken); Mermaid parse of changed blocks; `git diff --stat` readback.
- Delivery strategy: single-pr. Forecast ~150 authored changed lines. Branch `docs/seminar-alignment` stacked on `docs/system-design` (PR #1 not merged).

## Tasks
- [ ] T1 ADR scope notes (0004, 0007, 0014, 0015, 0016, 0017, 0018), ADR index, ADR-0021 lists 0018 — route: delegated (writer trigger: 9 files)
- [ ] T2 Metrics/API/requirements scoping (11-metricas §4 + diagram, 04-api `/metrics`, 01-requisitos Grafana) — route: delegated (same writer)
- [ ] T3 Assistant grounded on sensors and ML: `assistant --> telemetry` in 05, facts in 06 §9 (latest readings/daily aggregates, active alerts, risk probability + `model_version` + validation metric), ADR-0007 LangGraph alternative row — route: delegated (same writer)

## Acceptance criteria
- No ADR or doc presents a production-only piece as the seminar default without a scope note.
- The assistant sequence and module graph show sensor and ML inputs; the prompt rules still forbid the LLM from computing irrigation, risk or doses.
- ADR-0007 records why LangGraph is not used.
- 0 broken internal links; all changed Mermaid blocks parse.

## Progress / evidence
- Audit (2 Sonnet explore agents) + parent spot check of 0014/0016/0004/0017/0018, 11-metricas §4 and line 107, ADR index.

## Next step
T1–T3 via one writer.
