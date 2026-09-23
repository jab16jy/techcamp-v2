---
version: 1
slug: "web-src"
primary_target: "web/src"
related_targets: []
---

# Surface brief — TechCamp PWA shell and design system (E1)

Scope: design-system tokens, primitives, domain components, patterns, the AppShell (bottom tab bar) and the dev catalog `/dev/ui`. Visitor mode: **Operate**. Build path: code-led (no image generation available).

Audience and job: producer outdoors on a low-end Android, one hand, direct sun, weak signal; technician across many farms. Task on every future screen: read today's state, act, log. Constraints: PRODUCT.md (AAA on critical data, 48 px targets, ≤ 200 KB JS, system fonts, no 3D, no blur, color never alone).

## Direction contract

THESIS: The phone as a field-issued label, not a dashboard. Every plot, alert and reading is a card that ends in a **status band**, the way an insumo sack label ends in its colored category band: color + pictogram + one plain word, readable at arm's length in sun. Refuses the category default of chart-first KPI tiles and soft gray dashboards.

OWN-WORLD: iOS structure (owner-pinned): large titles, inset grouped lists, bottom tab bar, bottom sheets. Cream ground (paper-bag, not ivory), deep field green ink and brand, one warm annatto accent reserved for alerts; status bands ok green / watch ochre / irrigate water blue / stress clay red. System sans for UI and data with tabular numerals; system serif (`ui-serif`) only for large titles. Hairline rules, 12–16 px radii, no shadows except sheets, no gradients, no blur.

STORY: The user understands in one glance what state each thing is in and when the data was last seen, believes the numbers because units are explained and freshness is printed, and acts through the primary action at thumb reach.

FIRST VIEWPORT: `/dev/ui` at 390 px: large serif title "Sistema de diseño", an inset list of sections; first section shows the four status bands full-width stacked with pictogram + word + example sentence ("Hoy: regar 12 mm ≈ 40 min"), then the connection line printed as text under the title ("Sin conexión · 3 por subir · dato de hace 12 min"). Bottom tab bar fixed with five tabs; primary action button sits above it.

FORM: Seed-assigned grounded candidate 4 of 7 (insumo sack label with category band), fused into the owner-pinned iOS + Fruit Garden world. Raises: from the phosphor terminal — system states print themselves as text lines, not chrome badges; from the exposure record — every number sits in ruled, tabular, unit-labelled rows. Seed key 25e60b41.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

Signature interaction: pressing a status card lifts its band into a bottom sheet with the "why" (rationale) — same band, same word, expanded. Motion grammar: sheets slide up 240 ms ease-out, nothing else moves; respects reduced motion.

Unresolved: real field validation of palette in sunlight with a phone (docs/07 requires it) happens after E1.
