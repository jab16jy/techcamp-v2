# 07 — Frontend y design system

El principal problema de la v1 era visual y estructural: pantallas hechas una por una, sin tokens ni componentes compartidos. En la v2 **el design system se construye primero**. Ninguna pantalla usa colores, espacios o tamaños que no salgan de los tokens ([ADR-0006](adr/0006-design-system.md)).

## Principios de diseño para campo

| Principio | Regla concreta |
|---|---|
| **Legible a pleno sol** | Tema claro de alto contraste por defecto. Texto con contraste ≥ 7:1 (AAA) en datos críticos; ≥ 4,5:1 en el resto. |
| **Dedos grandes, manos ocupadas** | Áreas táctiles ≥ 48 × 48 px. La acción principal va al alcance del pulgar (barra inferior o botón flotante). |
| **Una decisión por pantalla** | La pantalla de inicio responde "¿qué hago hoy en esta parcela?" antes que cualquier gráfico. |
| **El color nunca va solo** | Todo estado (ok / vigilar / regar; info / advertencia / crítico) lleva icono y texto además del color. |
| **Honesto con la conexión** | Siempre visible: sin conexión, pendientes por subir, hora del último dato. |
| **Lenguaje simple** | Español llano, unidades explicadas ("12 mm ≈ 40 minutos de riego"). Nada de jerga técnica en la vista del productor. |
| **Liviano** | Bundle inicial ≤ 200 KB gzip. Fuente del sistema (sin descargar web fonts). Mapa y gráficos se cargan de forma diferida. |

## Capas del design system

```mermaid
flowchart TB
  t1[Tokens primitivos<br/>paleta, escala de espacio, radios, tipografía] --> t2
  t2[Tokens semánticos<br/>surface, text, border, status-ok/watch/irrigate,<br/>severity-info/warning/critical] --> p
  p[Primitivos UI<br/>shadcn/ui sobre Radix: Button, Input, Dialog,<br/>Sheet, Tabs, Toast, Select] --> c
  c[Componentes de dominio<br/>StatusBadge, MetricTile, AlertCard, WaterGauge,<br/>SyncIndicator, PlotMap, TimeSeriesChart] --> pt
  pt[Patrones<br/>AppShell con barra inferior, FormSheet,<br/>EmptyState, OfflineBanner, ListWithFilters] --> f
  f[Features / pantallas<br/>containers que conectan datos con componentes]
```

| Capa | Dónde vive | Regla |
|---|---|---|
| Tokens | `web/src/design-system/tokens.css` (`@theme` de Tailwind v4 más variables CSS) | Única fuente de color, espacio, radio y tipografía. El lint prohíbe valores arbitrarios (`bg-[#…]`, `p-[13px]`). |
| Primitivos | `web/src/design-system/ui/` | Componentes shadcn/ui copiados al repositorio y adaptados a los tokens. Son código propio, no una dependencia. |
| Componentes de dominio | `web/src/design-system/components/` | Presentacionales: reciben props y no piden datos. |
| Patrones | `web/src/design-system/patterns/` | Composiciones reutilizables de pantalla. |
| Catálogo | Ruta `/dev/ui` (solo en desarrollo) | Muestra cada componente en todos sus estados. Si el catálogo crece mucho se migra a Storybook. |

### Tokens semánticos iniciales

| Token | Uso |
|---|---|
| `--color-surface`, `--color-surface-raised` | Fondos |
| `--color-text`, `--color-text-muted` | Texto |
| `--color-brand` | Acciones principales |
| `--color-status-ok` / `-watch` / `-irrigate` / `-stress` | Estado del balance hídrico (`stress` solo en secano) |
| `--color-severity-info` / `-warning` / `-critical` | Severidad de alertas |
| `--color-offline` | Indicador sin conexión |
| `--space-1 … --space-8` | Escala de 4 px |
| `--radius-sm / md / lg` | Radios |
| `--text-sm / base / lg / xl / 2xl` | Base de 16 px; los datos clave en `2xl` |

Los valores concretos (paleta y escalas) se definen en la tarea de implementación del design system y se validan en exteriores con un teléfono real.

## Arquitectura del frontend

Carpetas por feature (screaming architecture) y patrón container/presentational:

```
web/src/
├── app/                 # router, providers, AppShell, manejo de errores
├── design-system/       # tokens, ui/, components/, patterns/
├── features/
│   ├── plot-status/     # pantalla de inicio de la parcela
│   ├── alerts/
│   ├── logbook/
│   ├── plots/           # fincas, parcelas, mapa, ciclos
│   ├── nodes/           # alta por QR, calibración, salud
│   ├── irrigation/
│   ├── metrics/
│   ├── assistant/
│   └── auth/
│       └── (cada feature) api/ · containers/ · components/ · routes.tsx
└── lib/
    ├── api/             # cliente generado desde OpenAPI
    ├── db/              # esquema Dexie
    └── sync/            # sincronizador de la bitácora
```

| Regla | Detalle |
|---|---|
| Container | Usa hooks de datos (TanStack Query / Dexie) y pasa props. No tiene estilos propios más allá del layout. |
| Presentational | Sin fetch ni stores. Se puede probar y mostrar en el catálogo de forma aislada. |
| Entre features | Una feature no importa otra; lo compartido baja a `design-system/` o `lib/` (lo verifica ESLint). |
| Estado del servidor | TanStack Query. El estado de UI global mínimo (organización y parcela activas) va en un store pequeño; no hay stores de datos del servidor. |

### Flujo de datos y offline

```mermaid
flowchart LR
  ui[Containers] -- lee/escribe bitácora --> dx[(Dexie<br/>IndexedDB)]
  ui -- consultas --> q[TanStack Query]
  q -- persistencia de caché --> dx
  q -- HTTP --> api[(API)]
  dx -- outbox --> sy[Sincronizador] -- push / pull --> api
  sw[Service Worker<br/>Workbox] -- precache del shell --> ui
  api -- SSE --> q
```

| Dato | Estrategia |
|---|---|
| Shell de la app (JS, CSS, íconos) | Precache del Service Worker: abre sin red |
| Estado de parcela, alertas, clima | TanStack Query con caché persistida en IndexedDB: offline se ve el último estado con su hora |
| Bitácora | **Local primero**: se escribe en Dexie y el sincronizador la sube ([06 §7](06-diseno-detallado.md#7-sincronización-offline-de-la-bitácora)) |
| Eventos en vivo | SSE invalida o actualiza las consultas afectadas |
| Teselas del mapa | Caché en tiempo de ejecución (las últimas vistas); mapas offline completos quedan diferidos (RF-18) |

## Mapa de pantallas

```mermaid
flowchart TB
  login[Ingreso por teléfono] --> consent[Consentimiento de datos] --> home
  subgraph tabs [Barra inferior]
    home[Inicio: estado de la parcela]
    alerts[Alertas]
    log[Bitácora]
    plots[Parcelas y mapa]
    more[Más]
  end
  home --> rec[Detalle de la recomendación de riego]
  home --> chart[Historial de sensores]
  home --> risk[Riesgo climático]
  alerts --> alertd[Detalle de alerta + reconocer]
  log --> newentry[Nueva entrada: labor, insumo, riego, cosecha, costo, foto]
  plots --> plotd[Parcela: polígono, suelo, ciclo]
  plotd --> nodes[Nodos de la parcela]
  nodes --> claim[Alta por QR + calibración]
  more --> assist[Asistente]
  more --> metrics[Indicadores de tecnificación]
  more --> org[Organización y miembros]
  more --> settings[Ajustes y notificaciones]
```

**Inicio (pantalla más importante):**

1. Parcela activa y cultivo con su etapa ("Maíz · día 42 · desarrollo").
2. Tarjeta de decisión: **"Hoy: regar 12 mm (≈ 40 min)"** o **"Hoy no necesita riego"**, con el porqué a un toque. En una parcela de secano no muestra lámina ni minutos, sino el déficit (**"Al cultivo le faltan 80 mm: está en estrés"**), la lluvia esperada en 7 días y el consejo del día (**"Cubra el suelo con rastrojo para conservar la humedad"**) ([ADR-0023](adr/0023-parcelas-con-riego-y-secano.md)).
3. Alertas abiertas.
4. Humedad de suelo actual con su hora y el pronóstico de 3 días.
5. Estado de sincronización y de los nodos.

## Presupuestos y calidad

| Métrica | Presupuesto | Verificación |
|---|---|---|
| JS inicial | ≤ 200 KB gzip | `size-limit` en CI |
| LCP (3G rápido, gama baja) | ≤ 2,5 s | Lighthouse CI |
| PWA instalable y offline | Obligatorio | Playwright en modo offline |
| Accesibilidad | 0 violaciones serias | `axe` en Playwright |
| Componentes | Todos en `/dev/ui` con sus estados | Revisión de PR |
