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
- Slice 2 = T3 — commit `f1cc675`.
- Slice 3 = T4 — commit `cddf783`.
- Slice 4 = T5 + T6 — commits `20b7bc9`, `5b099c5`.
- Slice 5 = T7 — commit `53d5d28` (+ inspect-round fix `283bc0b`).
- Slice 6 = T8 — commit TBD.
No push, no PR opened by this worker; the parent opens PRs per slice.

## Tasks
- [x] T1 `PRODUCT.md`, surface brief with direction contract, `.gitignore` keeps durable `.impeccable/` files — route: inline
- [x] T2 Tailwind v4 + `tokens.css` (primitive → semantic), ESLint ban on arbitrary values — route: delegated (commit a5e893e)
- [x] T3 Primitives in `design-system/ui/` (Button, Input, Dialog, Sheet, Tabs, Toast, Select) — route: delegated (commit f1cc675)
- [x] T4 Domain components in `design-system/components/` with tests for logic — route: delegated (commit cddf783)
- [x] T5 Patterns + router + AppShell with bottom tab bar — route: delegated (commit 20b7bc9)
- [x] T6 `/dev/ui` catalog, every component in all states (dev only) — route: delegated (commit 5b099c5)
- [x] T7 PWA: manifest, icons, Workbox precache, offline open; size-limit in CI — route: delegated (commit 53d5d28)
- [x] T8 Finish review (impeccable-finish-reviewer), fixes, `DESIGN.md` + `.impeccable/design.json` (impeccable-documenter) — route: delegated

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
- T3 (commit f1cc675): shadcn/ui primitives on Radix, copied into `web/src/design-system/ui/`, adapted to T2's token utilities only (no arbitrary values). Files: `button.tsx`, `input.tsx`, `dialog.tsx`, `sheet.tsx` (+ `sheet.css` for the slide-up-from-bottom animation, 200–250ms ease-out, respects `prefers-reduced-motion`), `tabs.tsx`, `toast.tsx` (built on `sonner`, the current shadcn-recommended toast path), `select.tsx`, `icons.tsx`, `utils.ts` (shared `cn()` via `clsx` + `tailwind-merge`). Deps added: `@radix-ui/react-dialog`, `@radix-ui/react-select`, `@radix-ui/react-tabs`, `class-variance-authority`, `clsx`, `sonner`, `tailwind-merge`.
  - Checks (re-run by the parent after a rate-limit interruption cut the writer off mid-report): `npm run lint` — clean; `npm run typecheck` — clean; `npm test -- --run` — 1 file, 1 test, passed; `npm run build` — succeeded, JS `219.62 kB` / gzip `68.58 kB` (unchanged from T2 — primitives aren't imported by any consumer yet, so nothing new is bundled; consumers land in T4/T5).
  - TDD: not required (no behavioral logic in T3, per Route and checks) — verified through the checks above; visual state coverage deferred to the `/dev/ui` catalog (T6).
  - Gaps/uncertain: none reported before the interruption. Button/Input/Tabs use plain elements + Radix only where Radix has a primitive (Dialog, Sheet-on-Dialog, Tabs, Select); Toast uses `sonner` rather than a hand-rolled Radix Toast, matching shadcn/ui's current recommended path.

- T4 (commit `cddf783`): Domain components, presentational only, in `web/src/design-system/components/`: `StatusBand`/`StatusBadge` (status→sack-label-band mapping: color + pictogram + one plain word), `MetricTile` (value + unit + freshness), `AlertCard` (severity info/warning/critical, distinct hues from status), `WaterGauge` (progressbar + printed percentage, color never alone), `SyncIndicator` (connection-honesty text line). Two pure functions carry the behavioral logic and were TDD'd: `getStatusConfig`/`getSeverityConfig` (`status.ts`/`severity.ts`) and `formatFreshness`/`formatSyncStatus` (`format.ts`, shared by MetricTile and SyncIndicator). Added 7 new stroke icons to `design-system/ui/icons.tsx` (status pictograms, alert severities, tab-bar glyphs) matching the existing single-stroke idiom; exported `IconProps`.
  - TDD: RED observed (`npm test -- --run` failed with "Failed to resolve import" for `./status`, `./severity`, `./format` — modules didn't exist yet), then implemented, then GREEN (16/16 tests passed).
  - Checks: `npm run lint` — clean; `npm run typecheck` — clean; `npm test -- --run` — 4 files, 16 tests, passed; `npm run build` — succeeded, JS unchanged at 219.62 kB / gzip 68.58 kB (components not yet consumed by any route).
- T5 (commit `20b7bc9`): Added `react-router` (`^8.4.0`). Patterns in `web/src/design-system/patterns/`: `AppShell` (fixed bottom tab bar, 5 tabs, iOS structure, Android back untouched — plain router links, no custom swipe-back), `FormSheet` (wraps the T3 Sheet primitive, primary action at thumb reach), `EmptyState`, `OfflineBanner` (wraps SyncIndicator), `ListWithFilters` (generic, filter row + inset grouped list). Router wiring: `web/src/app/routes.tsx` (`buildRoutes`, pure and tested) + `web/src/app/router.tsx` (the actual `createBrowserRouter` instance) + `web/src/app/PlaceholderPage.tsx` (stand-in for the 5 tab screens — no feature folders yet, per docs/07, since none need one). `App.tsx` now mounts `RouterProvider`; `App.test.tsx` updated accordingly. A minimal `web/src/dev-ui/DevUiCatalog.tsx` stub was added so the dev route resolves (T6 replaces its content).
  - Dev-route gating (TDD, called out explicitly in the task's TDD list): RED observed (`buildRoutes` import failed, module didn't exist), then implemented `buildRoutes(devRoute: RouteObject | null)` — pure, nests `devRoute` under the app shell only when supplied — then GREEN (3/3 new tests). The actual dev/prod decision lives in `router.tsx` as a **direct top-level** `if (import.meta.env.DEV) { ... }` (not passed through a function parameter) specifically so Vite/Rollup can prove the branch dead in production and drop the dynamic import's chunk entirely, rather than merely skip it at runtime — verified empirically (see T6 evidence: bundle byte-identical with/without the catalog's code).
  - Checks: `npm run lint` — clean; `npm run typecheck` — clean; `npm test -- --run` — 5 files, 19 tests, passed; `npm run build` — succeeded, JS 342.57 kB / gzip 107.96 kB gzip (jump is `react-router`; still well under the 200 KB budget); confirmed no separate `/dev/ui` chunk and no catalog text in `dist/assets/*.js`.
- T6 (commit `5b099c5`): Built the full `/dev/ui` catalog (`web/src/dev-ui/DevUiCatalog.tsx`), nested under `AppShell` (so it gets the real tab bar) at `dev/ui`. First viewport matches the direction contract: large serif title "Sistema de diseño", the connection-honesty line right under it (`SyncIndicator` demo, offline/3-pending/12-min-old — the exact contract example), then an inset list of the 8 sections, first section = the four status bands full-width stacked with pictogram + word + example sentence. Every component from T3/T4/T5 appears in every state: Button (3 variants × disabled × loading), Input (default/disabled/invalid), Select, Tabs, Dialog, Toast (via `sonner`), the 4 status bands/badges, 4 metric tiles (incl. no-data), 3 alert severities, 3 water-gauge levels, 3 sync-indicator states, FormSheet (live trigger), EmptyState (×2, incl. the list's own empty state), OfflineBanner (×2), ListWithFilters (live filter demo over synthetic plots). All demo content labeled synthetic in a header note, per PRODUCT.md "Evidence on Hand".
  - Checks: `npm run lint` — clean; `npm run typecheck` — clean; `npm test -- --run` — 5 files, 19 tests, passed; `npm run build` — succeeded, JS **342.57 kB / gzip 107.96 kB — byte-identical to T5's build**, despite the catalog importing Radix Select/Tabs and `sonner` (previously unused anywhere) — direct evidence the dev-only route and its dependencies are fully excluded from the production bundle, not just deferred.
- T7 (commit `53d5d28`): Installed `vite-plugin-pwa` (`^1.3.0`, generateSW/Workbox strategy) and `size-limit` + `@size-limit/file`. `web/vite.config.ts`: `VitePWA({ registerType: 'autoUpdate', manifest: {...}, workbox: { globPatterns: [...] } })` — manifest in Spanish (`lang: 'es'`, name "TechCamp"), `background_color`/`theme_color` taken straight from `--color-surface`/`--color-brand` tokens. Icons: hand-authored SVG mark (brand-green rounded square, cream droplet — the same silhouette as the `DropletIcon`/irrigate pictogram), rendered to PNG with ImageMagick (`magick -background none <svg> -resize <N>x<N> <out>.png`) at 192 and 512 ("any"), plus a 512 maskable variant with the droplet kept inside the ~80% safe zone (checked corner distances against the 205 px safe radius). Provenance: authored 2026-09-23 by this session, source SVGs kept at `web/src/assets/icon-source*.svg`; replaces the Vite-scaffold `favicon.svg`. Added `<meta name="theme-color">` to `index.html`. `size-limit` config in `package.json` checks `dist/assets/*.js` gzip against the 200 KB budget; wired into `.github/workflows/ci.yml` web job as `npm run build` + `npm run size` (the job had no build step before this).
  - Checks: `npm run lint` — clean; `npm run typecheck` — clean; `npm test -- --run` — 5 files, 19 tests, passed; `npm run build` — succeeded, generated `dist/sw.js`, `dist/workbox-*.js`, `dist/manifest.webmanifest`, `dist/registerSW.js`, precache 13 entries (401.18 KiB); `npm run size` — **107.05–107.98 kB gzip vs 200 KB budget, pass** (small run-to-run variance from content-hashed filenames).
  - TDD: not applicable — no new behavioral logic (config/build wiring only), per the task's TDD scope list.
- Inspect round (one batch, fix commit `283bc0b`): Served `/dev/ui` via `npm run dev` (port 5183; the catalog doesn't exist in a production build by design) and captured with `playwright-cli`: `.impeccable/review/desktop.png` (1440×3745, full page) and `.impeccable/review/mobile.png` (390×3861, full page) — both opened and confirmed valid PNGs. **Offline check** on the `npm run build && npm run preview` output (port 5184): waited for `navigator.serviceWorker.controller`, set the browser context offline, reloaded — the app rendered fully (large title, tab bar, active "Inicio" tab; screenshot `.impeccable/review/offline-check.png`), confirming it opens offline after one online visit.
  - Material gap found and fixed in one batch: the desktop capture showed content stretching edge-to-edge at 1440 px (no max-width), violating the craft-floor body-measure guidance and looking unrefined for a phone-first field app. Fixed by constraining `AppShell`'s `<main>` and the fixed tab bar to `max-w-md`, centered (no effect ≤448 px, i.e. no change on the mobile capture). Also stopped the lone "Abrir hoja de formulario" demo button from stretching full width in its vertical stack (`self-start`). Recaptured once (both screenshots re-saved, re-verified as valid PNGs); did not iterate further, per the skill's bounded-inspection rule.
  - Noted, not fixed: the fixed bottom tab bar appears to visually "cut into" the middle of the page in the *full-page* screenshots — this is a known Playwright/Chromium artifact of capturing `position: fixed` elements in full-page mode (the nav is stamped at its fixed viewport offset into the stitched image); it is correct and required behavior in real, non-stitched scrolling and was not treated as a defect.
  - `impeccable detect --json web/src`: **no findings** (`[]`).
- Authored-line delivery evidence (excluding `web/package-lock.json`, which only reflects `npm install` bookkeeping for `react-router`/`vite-plugin-pwa`/`size-limit`): T4–T7 + the inspect fix = 35 files changed, 1025 insertions(+), 8 deletions(-) (`git diff --shortstat 2a60223..HEAD -- . ':(exclude)web/package-lock.json'`). Full branch since `befa021`: 52 files changed, 10269 insertions(+), 2056 deletions(-) including the lockfile.

## Finish review
Disposition: **fix**. `impeccable-finish-reviewer` found 5 material issues against the direction contract; applied all 5 in one batch (commit `9382ca7`), route: delegated (sonnet-high, ponytail full).

1. MetricTile/WaterGauge were floating hero-metric cards (`rounded-lg border ... bg-surface-raised p-4`, small label + big number), contradicting FORM's "every number sits in ruled, tabular, unit-labelled rows" and the "every reading ends in a status band" thesis. Rebuilt both as bare rows (no self-shell) meant to sit inside an iOS inset-grouped-list container (`divide-y divide-text/10 rounded-lg border bg-surface-raised`, the same convention already used by `ListWithFilters`); each row now takes a required `status: StatusState` prop and ends in a `StatusBadge` (color + pictogram + word, reusing `getStatusConfig` unchanged — no new classification logic, so no RED/GREEN cycle was needed). `WaterGauge` keeps its progress bar under the row. Updated the only consumer (`DevUiCatalog.tsx`, Métricas/Nivel de agua sections) to pass a `status` and wrap the rows in the grouped-list container. Added `MetricTile.test.tsx` and `WaterGauge.test.tsx` (label/value/unit/freshness text and the status-band word render; WaterGauge clamp still verified).
2. `font-serif` leaked past large titles: `sheet.tsx:80` (`SheetTitle`), `dialog.tsx:77` (`DialogTitle`), `DevUiCatalog.tsx:49` (section `<h2>`). Switched all three to `font-sans font-semibold` (weight added to keep visual hierarchy without serif); the page-level `<h1>` large titles (catalog header, `PlaceholderPage`) were left untouched — serif stays reserved for those.
3. Verified after fix 1: `grep -rn "rounded-lg border border-text/10 bg-surface-raised p-4" web/src/design-system/components/` matches only `AlertCard.tsx` — no other domain component falls back to that shell.
4. Signature interaction: new `PressableStatusBand.tsx`, composed from the existing `StatusBand` + `Sheet`/`SheetTrigger` primitives (no new motion code — reuses `sheet.css`'s 240 ms ease-out slide-up and its `prefers-reduced-motion` guard unchanged). A native `<button>` wraps the band and opens the sheet via `SheetTrigger asChild`; the sheet repeats the same band plus a `rationale` line (the "why"). Wired into `/dev/ui`'s "Bandas de estado" section (replacing the plain `StatusBand` demo, which kept the same full-width-stacked-bands first-viewport shape). Keyboard/screen-reader: native button + Radix Dialog primitive (focus trap, Escape-to-close, `role="dialog"`) — same mechanism already used by `Dialog`/`FormSheet` elsewhere in the system. Added `PressableStatusBand.test.tsx` (button hides the rationale until pressed; pressing it reveals the sheet with the rationale and the same status word in both trigger and sheet).
5. Theme text selection/caret/scrollbar: added a browser-surfaces block to `tokens.css` (`caret-color`, `::selection`, `scrollbar-color` plus `::-webkit-scrollbar-*`), all referencing existing `--color-*`/`--radius-lg` tokens — no new arbitrary values.

Kept untouched, per the reviewer's explicit "keep": the `StatusBand` system itself (color/pictogram/word mapping, `status.ts`) and `SyncIndicator`'s plain-text connection line.

Root-cause fix found along the way: adding a second test per component file (`MetricTile.test.tsx`, `WaterGauge.test.tsx`, `PressableStatusBand.test.tsx`) exposed that `@testing-library/react`'s automatic `afterEach(cleanup)` never registers, because Vitest's `globals` option is off in `vite.config.ts` and Testing Library's auto-registration looks for a global `afterEach`. Registered it explicitly once in `web/src/test/setup.ts` rather than adding `cleanup()` calls per test file.

Checks (`web/`): `npm run lint` — clean; `npm run typecheck` — clean; `npm test -- --run` — 8 files, 26 tests, passed; `npm run build` — succeeded, JS `342.61 kB` / gzip `107.98 kB`; `npx size-limit` — **107.07 kB gzip vs 200 KB budget, pass**.

Recapture (`npm run dev`, `playwright-cli`, motion settled before each capture): `.impeccable/review/desktop.png` (1440×3740, full page, opened and confirmed valid), `.impeccable/review/mobile.png` (390×3928, full page, opened and confirmed valid), `.impeccable/review/signature-sheet.png` (390×844 viewport, captured immediately after the click snapshot confirmed the sheet's `role="dialog"` was rendered — sheet fully open with the "Regar" band, its rationale, and the expanded band repeated below; opened and confirmed valid). The fixed bottom tab bar again "cuts into" the full-page stitched screenshots at its viewport offset — same known Playwright/Chromium full-page artifact already noted in the T4–T7 inspect round, not a regression.

`impeccable detect --json web/src`: **no findings** (`[]`).

Delivery evidence for this batch (`git show --shortstat 9382ca7`): 11 files changed, 258 insertions(+), 34 deletions(-).

## Next step
`DESIGN.md` + `.impeccable/design.json` via `impeccable-documenter` — the remaining half of T8, out of this worker's authorized scope (this batch was fixes only); hand to the parent to delegate.

- T8: finish review (impeccable-finish-reviewer) disposition **fix** (5 material fixes) → batch `9382ca7` → verdict pass **ship** (all 5 resolved, no regressions; scope: those 5 fixes). Documenter wrote `DESIGN.md` + `.impeccable/design.json` (`2fc587f`). Documenter-reported drift (dead `--space-*` tokens) fixed in `abbf0f1` (`--spacing: 0.25rem` in `@theme`). Parent spot-check: lint, typecheck, 26 tests, build 107.98 kB gzip, size-limit pass.

## Review (RDD)
- Assessed per slice. Owner decision (2026-09-23): review only the high-risk slice; the medium slices (1–4, 6, 7) were explicitly left unreviewed. The design itself passed the impeccable finish review (ship).
- Slice 5 (`20b7bc9..87f1bab`: `/dev/ui`, PWA, CI): risk high (`ci.yml`). Owner granted consent. Lineage `review-24565c320ee15fac`, 4 lenses → **approved**, acknowledged, authority burned.
- 0 blocking findings; 6 informational, kept as follow-ups: PWA `autoUpdate` reloads the page without a prompt (can drop an in-progress form), the size-limit entry measures all JS rather than only the initial bundle, manifest colors hard-coded instead of linked to tokens, duplicated icon source with an undocumented PNG generation step, no automated PWA/offline assertion, and an over-claiming route test title.

## PR plan (stacked-to-main)
Authored lines exclude `package-lock.json` and PNG icons.
- PR 1: T1 + T2 `877b865`..`6cbdd77` — 289.
- PR 2: T3 primitives `f1cc675`, `2a60223` — 561 (shadcn/ui code copied into the repo; over budget).
- PR 3: T4 domain components `cddf783` — 427.
- PR 4: T5 patterns + router `20b7bc9` — 300.
- PR 5: T6 + T7 catalog, PWA, CI `5b099c5`..`87f1bab` — ~390 (RDD reviewed).
- PR 6: finish-review fixes `9382ca7`..`abbf0f1` — 328.
- PR 7: `DESIGN.md`, design sidecar and ODD close — ~430.
