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
- Delivery: strategy `ask-on-risk` → chain strategy `stacked-to-main` (owner, 2026-09-22); forecast ~1,800 authored lines (over budget). Branch `feat/e1-design-system` from `main` @ `befa021`.
- RDD: on (global). First boundary: branch point `befa021`.

## Slices
Small chained PRs, each targeting `main`, merged in order. Cut on task commit boundaries (each task closes with one clean work-unit commit); boundaries below are planned and adjusted as real sizes land.
- Slice 1 = T1 + T2 — commits `877b865`, `a5e893e` (+ doc bookkeeping `668e991`, `47bc289`).
- Slice 2 = T3 — commit TBD.
- Slice 3 = T4 — commit TBD.
- Slice 4 = T5 + T6 — commits TBD.
- Slice 5 = T7 — commit TBD.
- Slice 6 = T8 — commit TBD.
No push, no PR opened by this worker; the parent opens PRs per slice.

## Tasks
- [x] T1 `PRODUCT.md`, surface brief with direction contract, `.gitignore` keeps durable `.impeccable/` files — route: inline
- [x] T2 Tailwind v4 + `tokens.css` (primitive → semantic), ESLint ban on arbitrary values — route: delegated (commit a5e893e)
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
- T2 (commit a5e893e): Tailwind CSS v4 (`tailwindcss@4.3.3`, `@tailwindcss/vite@4.3.3`, pinned as `^4.3.3` by npm at install time — resolved as latest v4, not guessed) wired via `web/vite.config.ts` (`tailwindcss()` plugin) and `web/src/index.css` (`@import "tailwindcss"` + `@import "./design-system/tokens.css"`). `web/src/design-system/tokens.css` defines the primitive palette as plain `:root` custom properties, mapped to the required semantic tokens inside `@theme`, plus `--space-1..8` (4px scale, kept as plain `:root` vars — Tailwind v4's own namespace for a spacing scale is `--spacing-*`, not `--space-*`; docs/07 pins the exact name `--space-*`, and Tailwind's default `--spacing` multiplier (0.25rem = 4px) already keeps `p-1`/`gap-2`/etc. on the same 4px scale, so no override of Tailwind's own utility scale was needed) and `--radius-sm/md/lg` / `--text-sm/base/lg/xl/2xl` inside `@theme` (those two namespaces are Tailwind v4's own, so the exact required names double as the real utility-generating theme keys). No `--font-*` tokens were added: Tailwind v4's default `--font-sans` and `--font-serif` are already full system-font stacks (verified via ctx7 `/tailwindlabs/tailwindcss.com` docs) — `ui-serif` already leads `--font-serif` — so the "system fonts only, no web fonts" contract is satisfied by Tailwind's defaults with zero added code.
  - Arbitrary-value lint ban: verified via ctx7/npm that `eslint-plugin-tailwindcss` ships a maintained v4-compatible branch (readme sourced from its `v4` branch, package `4.4.0`, `peerDependencies: { tailwindcss: "^4.0.0", eslint: "^9.0.0 || ^10.0.0" }`, requires Node ≥20.19 — this environment runs Node 22.23.1). Installed it and enabled only `tailwindcss/no-arbitrary-value: 'error'` in `web/eslint.config.js` (not the full `recommended` config, to keep the ban minimal per ponytail/task scope), with `settings.tailwindcss.cssConfigPath: './src/index.css'` so the rule resolves arbitrary values against our actual v4 theme. No custom hand-rolled rule was needed — the official plugin covers `className` string literals and `cn(...)`-style calls (its default `functions` list) out of the box. Sanity-checked with a throwaway fixture (`bg-[#ff0000] p-[13px]`) confirming both were flagged, then removed the fixture.
  - Chosen palette values (final, hex):

    | Token | Value | Notes |
    |---|---|---|
    | `--color-surface` | `#efe7d6` | cream ground, paper-bag warm off-white |
    | `--color-surface-raised` | `#faf6ec` | lighter raised card/cell surface |
    | `--color-text` | `#17301f` | deep field green ink |
    | `--color-text-muted` | `#4f5f53` | green-tinted secondary text (not gray) |
    | `--color-brand` | `#1f4a32` | deep field green, brand actions |
    | `--color-status-ok` | `#1f5e33` | status band |
    | `--color-status-watch` | `#654200` | status band, ochre |
    | `--color-status-irrigate` | `#164c68` | status band, water blue |
    | `--color-status-stress` | `#6b2a1d` | status band, clay red (rainfed only) |
    | `--color-severity-info` | `#2a4e8a` | alert severity, distinct blue from status-irrigate |
    | `--color-severity-warning` | `#8a5a0e` | alert severity, amber (distinct from status-watch) |
    | `--color-severity-critical` | `#8a3212` | annatto — reserved for alerts only, never a general accent |
    | `--color-offline` | `#5f564a` | muted neutral, not alarming |
    | `--space-1` … `--space-8` | `4px … 32px` | 4px scale |
    | `--radius-sm/md/lg` | `12px / 14px / 16px` | within the 12-16px contract range |
    | `--text-sm/base/lg/xl/2xl` | `0.875rem / 1rem / 1.125rem / 1.25rem / 1.5rem` | base = 16px |

  - Contrast ratios (WCAG relative-luminance formula, computed with a throwaway Node script, not committed — run once and discarded per ponytail):

    | Pair | Ratio | Required | Pass |
    |---|---|---|---|
    | text `#17301f` on surface `#efe7d6` | 11.54:1 | ≥4.5 | ✅ (AAA too) |
    | text `#17301f` on surface-raised `#faf6ec` | 13.16:1 | ≥4.5 | ✅ (AAA too) |
    | text-muted `#4f5f53` on surface `#efe7d6` | 5.52:1 | ≥4.5 | ✅ |
    | text-muted `#4f5f53` on surface-raised `#faf6ec` | 6.29:1 | ≥4.5 | ✅ |
    | brand `#1f4a32` bg, white text | 10.08:1 | ≥4.5 | ✅ |
    | status-ok `#1f5e33` bg, white text | 7.76:1 | ≥7 (critical data) | ✅ |
    | status-watch `#654200` bg, white text | 9.00:1 | ≥7 (critical data) | ✅ |
    | status-irrigate `#164c68` bg, white text | 9.26:1 | ≥7 (critical data) | ✅ |
    | status-stress `#6b2a1d` bg, white text | 10.64:1 | ≥7 (critical data) | ✅ |
    | severity-critical (annatto) `#8a3212` bg, white text | 8.25:1 | ≥7 (critical data) | ✅ |
    | severity-warning `#8a5a0e` bg, white text | 5.92:1 | ≥4.5 | ✅ |
    | severity-info `#2a4e8a` bg, white text | 8.22:1 | ≥4.5 | ✅ |
    | offline `#5f564a` on surface `#efe7d6` | 5.85:1 | ≥4.5 | ✅ |
    | offline `#5f564a` on surface-raised `#faf6ec` | 6.67:1 | ≥4.5 | ✅ |

  - Checks: `npm run lint` — clean (0 errors); `npm run typecheck` — clean; `npm test -- --run` — 1 test file, 1 test, passed; `npm run build` — succeeded, JS `219.62 kB` / gzip `68.58 kB` (well under the 200 KB gzip budget, though that budget is formally verified with `size-limit` in T7).
  - Gaps/uncertain: none. The v4-compatible ESLint plugin existed and worked cleanly, so no hand-rolled rule was necessary (explicitly permitted either way by the task).

## Next step
T3 (primitives in `design-system/ui/`), delegated writer.
