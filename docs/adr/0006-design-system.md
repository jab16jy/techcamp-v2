# ADR-0006: Design system propio sobre Tailwind v4 + shadcn/ui (Radix), construido antes que las pantallas

- **Estado:** Aceptada
- **Fecha:** 2026-09-22

## Contexto

La v1 no tenía design system: cada pantalla definía sus colores, espacios y componentes. El resultado era inconsistente y difícil de mantener. La v2 se usa a pleno sol, en teléfonos pequeños y por personas con poco tiempo.

## Decisión

- **Tokens** (primitivos → semánticos) en CSS con `@theme` de Tailwind CSS v4: son la única fuente de color, espacio, tipografía y radios. El lint prohíbe valores arbitrarios.
- **Primitivos** de shadcn/ui (sobre Radix, accesibles) copiados al repositorio y adaptados a los tokens.
- Capas: tokens → primitivos → componentes de dominio → patrones → features (diseño atómico adaptado).
- Patrón **container/presentational** y carpetas por feature.
- Catálogo `/dev/ui` con todos los estados de cada componente.
- **El design system (E1) se hace antes que cualquier pantalla de feature.**

## Alternativas consideradas

| Alternativa | Por qué no |
|---|---|
| Tailwind sin design system (como la v1) | Es la causa del problema actual |
| MUI / Ant Design | Bundles pesados para 3G, estética difícil de adaptar al contexto de campo |
| CSS Modules a mano | Más código para lograr accesibilidad y consistencia que Radix ya resuelve |
| Storybook desde el día 1 | Infraestructura extra antes de tener componentes; se adopta cuando el catálogo lo justifique |

## Consecuencias

**Positivas**

- Consistencia visual y accesibilidad (foco, ARIA y teclado) de base.
- Los componentes son código propio: sin bloqueo por versiones de una biblioteca de UI.
- Las pantallas se arman más rápido cuando el design system existe.

**Negativas / costos aceptados**

- Retrasa las primeras pantallas visibles unas semanas.
- Los componentes copiados de shadcn/ui no se actualizan solos.

## Relacionado

[07-frontend-design-system](../07-frontend-design-system.md)
