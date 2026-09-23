# TechCamp v2 — E1 Design system + PWA shell

## Objective
Deliver epic E1 from `docs/10-dag.md`: tokens, primitives and the `/dev/ui` catalog; an installable PWA that opens offline. Built entirely through the `impeccable` skill (CLAUDE.md frontend rule).

## Why
ADR-0006: the design system comes before any feature screen. E8 and E9 depend on E1.

## Scope
- In: `PRODUCT.md`, impeccable surface brief (direction contract), Tailwind v4 tokens (primitive + semantic), lint ban on arbitrary values, shadcn/ui primitives adapted to tokens, domain components (StatusBand/StatusBadge, MetricTile, AlertCard, WaterGauge, SyncIndicator), patterns (AppShell with bottom tab bar, FormSheet, EmptyState, OfflineBanner, ListWithFilters), router, `/dev/ui` catalog (dev only), PWA (manifest, icons, precached shell, offline open), `size-limit` budget in CI, finish review and `DESIGN.md`.
- Out: feature screens and data (E3+), login screen (after E1 + E2), PlotMap and TimeSeriesChart (arrive with the features that use them: E3/E9), 3D (owner: no), web fonts (owner: system fonts only).

## Constraints
- Owner decisions 2026-09-22: iOS structure (large titles, grouped lists, bottom tabs, sheets), Fruit Garden palette mood (cream, deep green, one warm alert accent), system typography carefully set, no 3D, no `backdrop-filter`.
- `docs/07-frontend-design-system.md` principles and budgets: AAA on critical data, 48 px targets, color never alone, connection honesty, ≤ 200 KB initial JS gzip, lazy map/charts.
- Direction contract: `.impeccable/surfaces/web-src.md` (seed 25e60b41, code-led).
- Ponytail; current docs via ctx7; English code, Spanish UI copy.

## Route and checks
- TDD: on for behavioral logic (status mapping, freshness/sync formatting, routing guards). Source: CLAUDE.md (on from E2; E1 runs concurrently and follows it). Visual layers verified through the catalog and the finish review. Runner: `npm test` (Vitest) in `web/`.
- Other checks: `npm run lint`, `npm run typecheck`, `npm run build`, size-limit, offline open checked with `playwright-cli`.
- Route: T1 inline (parent, 3 files); T2–T7 delegated to one sonnet-high writer (writer trigger: 20+ non-trivial files; preparation trigger: impeccable references + design docs); T8 impeccable finish reviewer + documenter agents.
- Delivery: strategy `ask-on-risk`; forecast ~1,800 authored lines (over budget; chain strategy asked before PR). Branch `feat/e1-design-system` from `main` @ `befa021`.
- RDD: on (global). First boundary: branch point `befa021`.

## Tasks
- [x] T1 `PRODUCT.md`, surface brief with direction contract, `.gitignore` keeps durable `.impeccable/` files — route: inline
- [ ] T2 Tailwind v4 + `tokens.css` (primitive → semantic), ESLint ban on arbitrary values — route: delegated
- [ ] T3 Primitives in `design-system/ui/` (Button, Input, Dialog, Sheet, Tabs, Toast, Select) — route: delegated
- [ ] T4 Domain components in `design-system/components/` with tests for logic — route: delegated
- [ ] T5 Patterns + router + AppShell with bottom tab bar — route: delegated
- [ ] T6 `/dev/ui` catalog, every component in all states (dev only) — route: delegated
- [ ] T7 PWA: manifest, icons, Workbox precache, offline open; size-limit in CI — route: delegated
- [ ] T8 Finish review (impeccable-finish-reviewer), fixes, `DESIGN.md` + `.impeccable/design.json` (impeccable-documenter) — route: delegated

## Acceptance criteria
- [ ] No color, space, radius or type value outside tokens (lint enforced).
- [ ] `/dev/ui` shows every component in all its states.
- [ ] PWA installable; app opens offline after first load.
- [ ] Initial JS ≤ 200 KB gzip (size-limit); 0 serious axe violations on `/dev/ui`.
- [ ] Finish review verdict recorded; `DESIGN.md` written from the built world.

## Progress / evidence
- Branch created from `main` @ `befa021`.
- T1 (commit 877b865): PRODUCT.md (from docs/01, docs/07, owner decisions), surface brief verified (6 contract blocks + seed key present).

## Next step
T2–T7 (delegated writer).
