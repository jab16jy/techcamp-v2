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
- [x] T4 Detailed design, frontend/design system, ML, bottlenecks, DAG
- [x] T5 ADRs
- [x] T6 Mermaid validation + index
- [x] T7 ML rebuild: v1 data/artifacts lost + code audit → ADR-0010 superseded by ADR-0019, protocol ADR-0020, 08-ml rewritten, README differentiators

## Acceptance criteria
- Every step of the method has its own document.
- Every significant decision has an ADR with context, alternatives and consequences.
- All Mermaid blocks render.

## Progress / evidence
- T1: repository created at `~/proyectos/techcamp-v2`.
- T2: 00-glosario, 01-requisitos, 02-estimaciones, 11-metricas written.
- T3: 03-modelo-datos, 04-api, 05-arquitectura written.
- T4: 06-diseno-detallado, 07-frontend-design-system, 08-ml, 09-cuellos-de-botella, 10-dag written.
- T5: 18 ADRs (0001-0018) + adr/README.md index generated.
- T6: docs/README.md index. Link/anchor check: 0 broken. Mermaid: 23 blocks parsed with mermaid 12.0.0 (jsdom) → 0 failures; parser confirmed to reject an invalid sample. Full visual render (headless browser) not run: no Chromium available.
- Review assessment (base b142709, committed-only): risk=passive (non_executable_only), review_due=false. No review needed.

- T7: user confirmed v1 ML datasets/artifacts are unrecoverable (other machine). Code audit (delegated explore) found leakage (hard negatives cross split, rows duplicated before split), artificial prevalence, no tuning, no trivial baselines. Links: 0 broken; Mermaid: 23 blocks, 0 failures.

## Next step
User review of the design. Then start implementation with E0 (see docs/10-dag.md), as a new ODD feature document.
