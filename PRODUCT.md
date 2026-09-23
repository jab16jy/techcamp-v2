# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Producer (primary):** smallholder farmer in the Colombian Caribbean. Low-end Android phone, intermittent signal, direct sun, little time; most plots are rainfed, some irrigated. Job: know what to do with water today, receive alerts, log labor and harvests offline.
- **Technician / extension agent:** visits many farms for a cooperative or program. Job: see the state of many plots, install and calibrate nodes, record extension visits offline.
- **Organization admin:** manages members, farms and nodes; reads aggregate indicators.
- **Researcher:** exports anonymized data and evaluates models (secondary, not a UI driver).

Source: `docs/01-requisitos.md`.

## Product Purpose

Help producers and technicians **measure** what happens in the plot (sensors and field logbook), **decide** with that data (irrigate, protect, harvest) and **demonstrate** the impact of technification. Success: the home screen answers "what do I do today in this plot?" before any chart.

Current scope is a seminar project running locally with a node simulator and scenarios (ADR-0021).

## Positioning

The main data is produced in the field (sensors and logbook); public data and models only complement it. Daily water decision per plot from FAO-56, with an explicit rainfed path (deficit and management advice, no irrigation depth) instead of assuming irrigation (ADR-0023).

## Operating Context

Outdoors, often one-handed, under direct sun, with intermittent or no connectivity. Technicians work across several farms per day. Offline-first logbook and extension visits sync when signal returns.

## Capabilities and Constraints

- PWA: React 19, Vite, strict TypeScript, Tailwind CSS v4, shadcn/ui on Radix, TanStack Query, Dexie, Workbox. Leaflet and uPlot load lazily.
- Initial JS ≤ 200 KB gzip; LCP ≤ 2.5 s on fast 3G low-end device; installable and opens offline.
- Every state (ok / watch / irrigate / stress; info / warning / critical) carries icon and text, never color alone.
- Connection honesty: offline state, pending uploads and last-data time are always visible.
- Plain Spanish UI copy; units explained ("12 mm ≈ 40 minutos de riego"); no jargon in the producer view.
- Design system first: tokens → primitives → domain components → patterns → features; catalog at `/dev/ui` (ADR-0006, `docs/07-frontend-design-system.md`).

## Brand Commitments

Owner decisions, 2026-09-22 (binding, recorded without expansion):

- Interface structure in the iOS idiom: bottom tab bar, large titles, grouped lists, sheets for forms. Not iOS-only gestures (Android back must keep working).
- Palette mood taken from the "Fruit Garden" agritech reference: cream ground, deep green, one warm accent reserved for alerts.
- System typography only (no downloaded web fonts); headings may use the system serif stack (`ui-serif`). Typography must still be carefully set.
- No 3D and no translucent blur (`backdrop-filter`).

## Evidence on Hand

No real users, photos, logos or field data yet. Seminar data comes from the simulator and scenarios A–E. Any demo content in the catalog is synthetic and must be labeled as such.

## Product Principles

1. One decision per screen: today's water action comes first.
2. Legible in direct sun: AAA contrast (≥ 7:1) on critical data, ≥ 4.5:1 elsewhere.
3. Big fingers, busy hands: touch targets ≥ 48 × 48 px, primary action within thumb reach.
4. Honest with the connection.
5. Light: every byte earns its place on 3G.

## Accessibility & Inclusion

WCAG AAA contrast for critical data, AA elsewhere; zero serious axe violations; low digital literacy assumed, so labels are plain words next to icons.
