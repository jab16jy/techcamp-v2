/**
 * The words of the rest of the home screen (docs/07 §Mapa de pantallas, Inicio
 * items 3–5): the open alerts, the soil-moisture reading, the three forecast
 * days and the node rows.
 *
 * Pure, like `decisionCopy.ts`, so the presentational layer keeps no logic and
 * every sentence is asserted literally. Three rules run through it:
 *
 * - A rule code the server sends is named from the rule's own documented
 *   condition (docs/06 §3), never from a guessed translation, and a code this
 *   build does not know keeps itself as the label — an organization may add its
 *   own rules (docs/04 §Alertas), and the code is then the only truthful name.
 * - No number is printed that the server did not send. The alert `evidence`
 *   object is deliberately not read: its shape is per-rule and undocumented in
 *   the payload contract, and a home screen guessing at it would be inventing.
 * - Missing evidence is a dash or a sentence, never a zero: no `0 %` moisture,
 *   no `0 mm` rain, no `0 °C`.
 */
import type { components } from '../../../lib/api/schema'
import type { Severity } from '../../../design-system/components/severity'
import type { StatusState } from '../../../design-system/components/status'

type PlotStatus = components['schemas']['PlotStatusView']
export type OpenAlert = PlotStatus['open_alerts'][number]
export type WeatherDay = PlotStatus['weather_next_3d'][number]
export type NodeHealth = PlotStatus['nodes'][number]

/** The quiet line a plot with no open alerts gets. It teaches the section without
 * being loud: the screen has a place for alerts, and today it is empty. */
export const NO_OPEN_ALERTS = 'Sin alertas abiertas.'

const MM = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 1 })
const PCT = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 1 })

/** A number with its unit, or a bare dash when it is missing: no dangling unit
 * and no invented zero (`formatPercent` in the design system does the same). */
function withUnit(value: number | null, unit: string): string {
  return value === null ? '—' : `${MM.format(value)} ${unit}`
}

/** A missing number stays missing. */
function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const ALERT_COPY: Record<string, { title: string; description: string }> = {
  water_stress: {
    title: 'Estrés hídrico',
    description: 'La humedad del suelo está por debajo de lo que el cultivo necesita.',
  },
  waterlogging: {
    title: 'Encharcamiento',
    description: 'El suelo está más húmedo que su capacidad de campo.',
  },
  heat_stress: {
    title: 'Calor extremo',
    description: 'La temperatura del aire lleva más de 3 h por encima de 35 °C.',
  },
  fungal_risk: {
    title: 'Riesgo de hongos',
    description: 'La humedad relativa media del día pasó de 85 %.',
  },
  heavy_rain_forecast: {
    title: 'Lluvia fuerte prevista',
    description: 'Se espera más de 50 mm de lluvia en 24 h.',
  },
  flood_risk: {
    title: 'Riesgo de inundación',
    description: 'El modelo de clima marca un riesgo alto de inundación.',
  },
  drought_risk: {
    title: 'Riesgo de sequía',
    description: 'El modelo de clima marca un riesgo alto de sequía.',
  },
  node_offline: {
    title: 'Nodo sin señal',
    description: 'Un nodo de la parcela dejó de reportar lecturas.',
  },
  node_battery_low: {
    title: 'Batería del nodo baja',
    description: 'La batería de un nodo está por agotarse.',
  },
}

const KNOWN_SEVERITIES: readonly string[] = ['info', 'warning', 'critical']

export interface AlertView {
  severity: Severity
  title: string
  description: string
}

/**
 * One alert as the card prints it. The order is the server's (docs/04 §Estado:
 * `critical` first, then newest), and the home never re-sorts it.
 *
 * An unknown severity is shown as a warning: downgrading an alert this build
 * cannot classify to "Información" would understate a possibly critical event,
 * and the band always carries its word and pictogram, never colour alone.
 */
export function alertView(alert: OpenAlert): AlertView {
  const copy = ALERT_COPY[alert.rule_code]
  return {
    severity: (KNOWN_SEVERITIES as string[]).includes(alert.severity)
      ? (alert.severity as Severity)
      : 'warning',
    title: copy?.title ?? alert.rule_code,
    description: copy?.description ?? 'Revisa el detalle de la alerta.',
  }
}

export interface SoilMoisture {
  /** The reading, or an honest "Sin lectura" (docs/07: a missing datum is never a zero). */
  value: string
  /** The same band vocabulary as the decision card, from the plot's own threshold. */
  status: StatusState
}

/**
 * Item 4's reading. The stress word needs the plot's θ_estrés
 * (`water_balance.stress_moisture_pct`, ADR-0022): it is the only threshold the
 * payload carries, and the stress starts *below* it, so a reading exactly at the
 * threshold is already stressed. Without the threshold the home does not judge a
 * number it has nothing to compare it against.
 */
export function soilMoisture(
  latest: PlotStatus['latest'],
  waterBalance: PlotStatus['water_balance'],
): SoilMoisture {
  const reading = num(latest.soil_moisture_pct)
  if (reading === null) return { value: 'Sin lectura', status: 'watch' }

  const threshold = num(waterBalance?.stress_moisture_pct)
  if (threshold !== null && reading <= threshold) return { value: `${PCT.format(reading)} %`, status: 'stress' }
  return { value: `${PCT.format(reading)} %`, status: 'ok' }
}

export interface ForecastRow {
  day: string
  label: string
  rain: string
  temps: string
  /** The row's own `stale` flag (docs/04 §Lecturas y clima), shown, never hidden. */
  stale: boolean
}

const WEEKDAYS = new Intl.DateTimeFormat('es-CO', { weekday: 'long', timeZone: 'UTC' })

/** `YYYY-MM-DD` plus a whole day. These are already Bogotá dates, so plain UTC
 * arithmetic is the right one: converting through a local zone would shift them. */
function addDays(day: string, days: number): string | null {
  const parsed = new Date(`${day}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return null
  parsed.setUTCDate(parsed.getUTCDate() + days)
  return parsed.toISOString().slice(0, 10)
}

/** "Hoy", "Mañana", then the weekday — by the row's own date, not its position. */
function dayLabel(day: string, today: string): string {
  if (day === today) return 'Hoy'
  if (day === addDays(today, 1)) return 'Mañana'
  const parsed = new Date(`${day}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return day
  return WEEKDAYS.format(parsed)
}

/**
 * Item 4's three days. A day with one temperature extreme shows that one, and a
 * day with none shows a dash: "24– °C" would be a hole in a real range.
 */
export function forecastRows(days: WeatherDay[], today: string): ForecastRow[] {
  return days.map((day) => {
    const tmin = num(day.tmin_c)
    const tmax = num(day.tmax_c)
    const temps =
      tmin !== null && tmax !== null
        ? tmin === tmax
          ? `${MM.format(tmin)} °C`
          : `${MM.format(tmin)}–${MM.format(tmax)} °C`
        : tmin !== null
          ? `${MM.format(tmin)} °C`
          : tmax !== null
            ? `${MM.format(tmax)} °C`
            : '—'
    return {
      day: day.day,
      label: dayLabel(day.day, today),
      rain: withUnit(num(day.rain_mm), 'mm'),
      temps,
      stale: day.stale === true,
    }
  })
}

/**
 * The same four words `features/nodes/containers/PlotNodesSection.tsx` already
 * uses, so a node reads the same on both screens. `StatusBadge` is not used here
 * on purpose: it carries the plot's water-balance vocabulary (ok / watch /
 * irrigate / stress), which is a different vocabulary from a node's state
 * (DESIGN.md: two vocabularies, not interchangeable), so the word travels with
 * the row text.
 */
const NODE_STATE_LABELS: Record<string, string> = {
  provisioned: 'Sin vincular',
  online: 'En línea',
  offline: 'Sin señal',
  retired: 'Retirado',
}

export function nodeStateLabel(status: string): string {
  return NODE_STATE_LABELS[status] ?? status
}
