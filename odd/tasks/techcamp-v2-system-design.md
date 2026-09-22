# TechCamp v2 — System design

## Objective
Define the complete system design of TechCamp v2 (docs only, no code): requirements, estimations, data model, API, high-level and detailed design, bottlenecks, DAGs, ADRs and technification metrics, with Mermaid diagrams.

## Problem / why
TechCamp v1 (AgroCaribe AI) was built piece by piece without a design system or architecture baseline. v2 starts in a new repository (see ADR-0001) and needs an agreed design before implementation.

## Scope
- In: `docs/` system design following the karanpratapsingh/system-design interview method (requirements → estimation → data model → API → high-level → detailed → bottlenecks), `docs/adr/`, DAGs, metrics.
- Out: any source code, infrastructure, CI. Implementation starts after user review.

## Constraints
- Docs in neutral professional Spanish (v1 docs convention, Spanish-speaking team); code identifiers in English.
- Diagrams in Mermaid.
- Claims about v1 must cite v1 files/metrics.

## Route and checks
- TDD: off (docs only; AGENTS.md of v1 says TDD not enforced). Checks: Mermaid syntax render check, link check by readback.
- Route: direct inline. Writer trigger (2+ files) fired; kept inline deliberately because the design synthesis already lives in the parent context and a handoff would duplicate it. Deviation recorded here.
- Delivery strategy: single-pr (docs only).

## Tasks
- [x] T1 Repo init + root README + ODD doc
- [x] T2 Requirements, glossary, metrics, estimations
- [x] T3 Data model, API, high-level architecture
- [ ] T4 Detailed design, frontend/design system, ML, bottlenecks, DAG
- [ ] T5 ADRs
- [ ] T6 Mermaid validation + index

## Acceptance criteria
- Every step of the method has its own document.
- Every significant decision has an ADR with context, alternatives and consequences.
- All Mermaid blocks render.

## Progress / evidence
- T1: repository created at `~/proyectos/techcamp-v2`.
- T2: 00-glosario, 01-requisitos, 02-estimaciones, 11-metricas written.
- T3: 03-modelo-datos, 04-api, 05-arquitectura written.

## Next step
T4.
