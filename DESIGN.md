---
name: TechCamp
description: A phone-issued sack label for field water decisions — status bands you read in the sun, not a dashboard.
colors:
  surface: "#efe7d6"
  surface-raised: "#faf6ec"
  text: "#17301f"
  text-muted: "#4f5f53"
  brand: "#1f4a32"
  status-ok: "#1f5e33"
  status-watch: "#654200"
  status-irrigate: "#164c68"
  status-stress: "#6b2a1d"
  severity-info: "#2a4e8a"
  severity-warning: "#8a5a0e"
  severity-critical: "#8a3212"
  offline: "#5f564a"
typography:
  display:
    fontFamily: "ui-serif, Georgia, serif"
    fontSize: "1.5rem"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "normal"
  headline:
    fontFamily: "ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
  title:
    fontFamily: "ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 500
    lineHeight: 1.4
rounded:
  sm: "12px"
  md: "14px"
  lg: "16px"
  full: "9999px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "20px"
  6: "24px"
  7: "28px"
  8: "32px"
components:
  button-primary:
    backgroundColor: "{colors.brand}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "48px"
  button-secondary:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "48px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "48px"
  status-band:
    backgroundColor: "{colors.status-ok}"
    textColor: "{colors.surface-raised}"
    rounded: "{rounded.lg}"
    padding: "12px 16px"
  status-badge:
    backgroundColor: "{colors.status-ok}"
    textColor: "{colors.surface-raised}"
    rounded: "{rounded.full}"
    padding: "4px 12px"
  input:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "48px"
  card:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
    padding: "16px"
---

# Design System: TechCamp

## Overview

**Creative North Star: "The Sack-Label Phone"**

Every plot, alert, and reading is a card that ends in a status band, the way an insumo sack label ends in its colored category band: color, pictogram, and one plain word, readable at arm's length in direct sun. The structure underneath is the owner-pinned iOS idiom — large titles, inset grouped lists, a fixed bottom tab bar, bottom sheets for detail and forms — carrying a Fruit Garden palette (cream ground, deep field green, one warm accent held back for alerts only). The system refuses chart-first KPI tiles and soft gray dashboard chrome: numbers live in ruled, tabular, unit-labelled rows inside grouped lists, not floating hero cards.

The build is flat by declared invariant (no shadows except the one sheet surface), has no gradients, no blur, and no 3D. Its one motion event is the bottom sheet sliding up; nothing else animates. Pictograms are load-bearing, not decorative: every status band pairs an icon with its plain word so color never carries meaning alone.

**Key Characteristics:**
- Cream ground, deep green ink, one warm accent (annatto/severity-critical) reserved for alerts, never a general accent.
- Four water-balance status colors (ok / watch / irrigate / stress) and three alert severities (info / warning / critical) are two distinct, non-interchangeable vocabularies.
- Flat by default; the bottom sheet is the only surface allowed a shadow and the only element allowed to move.
- System fonts only: `ui-serif` for large titles, `ui-sans-serif` for everything else, tabular numerals on every reading.
- 12–16px radii throughout; fully-rounded pills for badges and filter chips only.

## Colors

Muted, low-chroma earth tones on a warm paper-bag cream ground; the one bright note is the annatto accent reserved for critical alerts.

### Primary
- **Brand Green** (`#1f4a32`): primary buttons, active tab label/icon, focus outline, caret, text selection.

### Neutral
- **Paper-Bag Cream** (`#efe7d6`): app background (`--color-surface`).
- **Raised Cream** (`#faf6ec`): cards, sheets, inputs, the tab bar itself (`--color-surface-raised`) — one step lighter than the ground, not a shadow.
- **Deep Field Green Ink** (`#17301f`): primary text (`--color-text`).
- **Muted Field Green** (`#4f5f53`): secondary text, freshness clauses, inactive tab labels (`--color-text-muted`).
- **Offline Clay-Gray** (`#5f564a`): the offline-state text color and the offline banner's tint (`--color-offline`).

### Named Rules
**The Two Vocabularies Rule.** Status (`ok` green / `watch` ochre / `irrigate` water blue / `stress` clay red) describes a plot's water balance. Severity (`info` blue / `warning` ochre / `critical` annatto) describes an alert. They share no color values and must never be used to represent each other's concept.

**The Annatto Rule.** `severity-critical` (`#8a3212`) is the one warm accent in the system, reserved for critical alerts. It never appears as a general-purpose accent, brand color, or decorative highlight — the codebase's own token comment marks it "alerts only, never a general accent."

## Typography

**Display Font:** `ui-serif` (system serif stack, e.g. Georgia)
**Body Font:** `ui-sans-serif` (system sans stack)

**Character:** A quiet serif reserved for a single large title per screen, set against sans-serif for every other word and every number — a label typeset, not a display one.

### Hierarchy
- **Display** (400, 1.5rem `ui-serif`, 1.3 line-height): the one large page title per screen (e.g. "Sistema de diseño"). Never used for section headers or body copy.
- **Headline** (600, 1.25rem, 1.3): section headers inside a screen (catalog section titles).
- **Title** (600, 1.125rem, 1.4): the plain word inside a status band, alert titles, empty-state titles, and the numeric readout in a metric row or water gauge.
- **Body** (400, 1rem, 1.5): status-band messages, field labels, list item text, input text.
- **Label** (500, 0.875rem, 1.4): freshness clauses, muted captions, filter-chip and badge text.

### Named Rules
**The One Serif Rule.** `ui-serif` appears exactly once per screen, on the large title. Every other piece of text — including section headers — is sans.
**The Tabular Numerals Rule.** Every field reading (metric values, water-gauge percentages) sets `tabular-nums` so digits don't jitter as they update.

## Layout

Single-column phone layout capped at a `max-w-md` (28rem) column, centered even on wider viewports — there is no multi-column desktop composition. Content scrolls under a `fixed` bottom tab bar (five destinations, 48px-minimum touch targets); the main content area reserves bottom padding (`pb-24`) so the tab bar never occludes the last row. Screens are built from inset grouped lists: rows are hairline-divided (`divide-text/10`) inside a single rounded, raised-cream container, not individually-carded rows. Spacing runs on a 4px rhythm (the codebase declares primitive steps 4/8/12/16/20/24/28/32px), most visibly as the standard row padding of 16px horizontal / 12px vertical.

## Elevation & Depth

Flat by declared invariant. There are no shadow tokens for resting surfaces — cards, list containers, and metric rows are distinguished from the ground only by the one-step lighter Raised Cream fill and hairline borders, never by a drop shadow. The single exception is the bottom sheet, which lifts on a real shadow (`shadow-lg`) as it slides in; this is the system's one "temporarily elevated" surface, not a resting one.

### Named Rules
**The Sheet-Only Shadow Rule.** Shadows exist nowhere except the bottom sheet. Every resting surface (card, list, tile, band, badge) stays flat; depth there is conveyed by tone (Raised Cream vs. Cream) and hairline dividers, not by shadow.

## Shapes

Two radius bands cover the whole system: 12–16px for rectangular surfaces (buttons, inputs, selects use 14px; bands, cards, list containers, and the sheet's top corners use 16px), and fully-rounded pills (`rounded-full`) for anything that reads as a token rather than a surface — status badges, severity badges, filter chips, and the progress-bar track/fill inside the water gauge. Borders are hairline (`border-text/10` to `/20`) and used sparingly: to separate rows inside a list, to outline secondary buttons and inputs, and to cap the sheet's top edge. No clipping effects, no asymmetric corners, no decorative geometry.

## Components

### Buttons
- **Shape:** rounded rectangle (14px radius), 48px minimum height, horizontal padding.
- **Primary:** brand-green fill, cream text; the sole call-to-action color in the system.
- **Secondary:** raised-cream fill with a hairline border, ink-green text.
- **Ghost:** transparent, ink-green text, a faint ink-tinted hover/active wash.
- **Hover / Focus:** primary darkens toward `brand/90` on hover and `brand/80` on active; every variant shows a 2px brand-colored focus-visible outline with 2px offset. A `loading` state shows an inline spinner without swapping out the label; disabled drops to 50% opacity and blocks pointer events.

### Status Band (signature component)
- **Shape:** full-width rounded rectangle (16px radius), status-colored fill, cream text.
- **Anatomy:** pictogram (24px icon) + plain word (title) + one example sentence (body), left-aligned, top-anchored icon.
- **Interaction:** pressing a status band (the `PressableStatusBand` pattern) lifts the same band — same color, same word — into a bottom sheet holding its rationale text. This is the system's signature interaction and its only sheet-triggering gesture reserved for status content.
- **Compact form:** `StatusBadge` is the same color/icon/word as a pill, used inline in list rows and metric tiles where the full band would be too heavy.

### Cards / Containers
- **Corner Style:** 16px radius.
- **Background:** Raised Cream, no border on plain cards; a hairline border on alert cards.
- **Shadow Strategy:** none (see Elevation & Depth) — flat at rest.
- **Internal Padding:** 16px.

### Inputs / Fields
- **Style:** 14px radius, hairline border (`text/20`), Raised Cream fill, 48px height.
- **Focus:** 2px brand-colored outline, 2px offset — the same focus treatment as buttons.
- **Error:** border and outline switch to `severity-critical` via `aria-invalid`; disabled drops to 50% opacity.

### Navigation
- **Style:** fixed bottom tab bar, five destinations, Raised Cream fill with a top hairline border. Icons are 24px line pictograms; label is 14px sans below the icon.
- **States:** active tab shows brand-green icon and medium-weight label; inactive tabs show muted-green. No badges or dots on tabs.

### Sheet (signature component)
- **Style:** slides up from the bottom edge, 16px top-corner radius, hairline top border, `shadow-lg` — the system's one elevated surface.
- **Motion:** 240ms ease-out slide on open and close, driven by a real CSS `animation` (not a transition) so Radix can unmount after it finishes; respects `prefers-reduced-motion` by collapsing the duration to near-zero. Nothing else in the system animates.

## Do's and Don'ts

### Do:
- **Do** pair every status or severity color with its pictogram and plain word (the color-never-alone rule the tokens enforce structurally).
- **Do** keep `ui-serif` to exactly one large title per screen; every other heading is sans.
- **Do** use 48px as the minimum interactive height for buttons, inputs, tabs, and list rows.
- **Do** print connection state (offline / pending uploads / data freshness) as a plain text line, never a chrome badge or icon-only indicator.

### Don't:
- **Don't** apply a shadow to any surface except the bottom sheet.
- **Don't** use `severity-critical` (annatto) as a general accent, brand highlight, or decorative color — it is reserved for critical alerts only.
- **Don't** cross the status and severity vocabularies (e.g. don't render a plot's water-balance state in a severity color or vice versa).
- **Don't** add gradients, blur (`backdrop-filter`), or 3D treatments; none exist in the shipped build.
