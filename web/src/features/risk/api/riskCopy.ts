import { formatPercent } from '../../../design-system/components/format'
import type { components } from '../../../lib/api/schema'

/** `risk_prediction` on the wire (docs/04-api.md §Riesgo, métricas y asistente). */
export type RiskPrediction = components['schemas']['RiskPredictionView']

/**
 * The events the risk module names (docs/03-modelo-datos.md §Riesgo).
 *
 * An event this build does not know keeps its own key on the wire and is left
 * out of the screen: a card labelled with a guess would tell the producer
 * something the server never said.
 */
const EVENT_NAMES: Record<string, string> = {
  flood: 'Inundación',
  drought: 'Sequía',
}

/**
 * The severities the risk module serves (docs/08-ml.md §M2 "Severidad"), in the
 * words of the reader.
 *
 * The risk vocabulary is its own — `low`/`high`/`critical` is not the alert
 * vocabulary (`info`/`warning`/`critical`), and a prediction is not an alert.
 */
const SEVERITY_WORDS: Record<string, string> = {
  low: 'Riesgo bajo',
  high: 'Riesgo alto',
  critical: 'Riesgo crítico',
}

/**
 * The M2 feature vector's names in the words of the reader (docs/08-ml.md §M2
 * "Features").
 *
 * A feature this build does not know keeps its own name and shows no unit: an
 * empty label would be a mystery and a wrong unit would be a lie, while the
 * raw name at least lets the producer find it in the model card.
 */
const FACTOR_LABELS: Record<string, string> = {
  precip_sum_1m: 'Lluvia acumulada (1 mes)',
  precip_sum_2m: 'Lluvia acumulada (2 meses)',
  precip_sum_3m: 'Lluvia acumulada (3 meses)',
  precip_sum_4m: 'Lluvia acumulada (4 meses)',
  precip_sum_5m: 'Lluvia acumulada (5 meses)',
  precip_sum_6m: 'Lluvia acumulada (6 meses)',
  precip_anomaly_1m: 'Lluvia fuera de lo normal (1 mes)',
  precip_anomaly_3m: 'Lluvia fuera de lo normal (3 meses)',
  precip_anomaly_6m: 'Lluvia fuera de lo normal (6 meses)',
  soil_moisture_mean_1m: 'Humedad del suelo (1 m)',
  elevation_m: 'Altitud',
  slope_deg: 'Pendiente',
  month_sin: 'Mes del año',
  month_cos: 'Mes del año',
}

/** The unit of each feature, from the same source as the names. */
const FACTOR_UNITS: Record<string, string> = {
  precip_sum_1m: 'mm',
  precip_sum_2m: 'mm',
  precip_sum_3m: 'mm',
  precip_sum_4m: 'mm',
  precip_sum_5m: 'mm',
  precip_sum_6m: 'mm',
  precip_anomaly_1m: 'mm',
  precip_anomaly_3m: 'mm',
  precip_anomaly_6m: 'mm',
  soil_moisture_mean_1m: 'm³/m³',
  elevation_m: 'm',
  slope_deg: '°',
}

export interface RiskFactorLine {
  /** The feature in the reader's words, or its model name when unknown. */
  label: string
  /** The feature's value with its unit, e.g. "241,0 mm". */
  value: string
}

export interface RiskEventLine {
  /** The event in plain words, or `null` when this build does not know it. */
  event: string | null
  /** The probability as a percentage, e.g. "8,5 %". */
  probability: string
  /** The horizon in plain words, e.g. "1 de noviembre → 30 de noviembre · 30 días". */
  horizon: string
  /**
   * The severity, or `null` when the version that answered has no calibrated
   * cuts.
   *
   * The served line base is registered with `thresholds={}`, so its severity is
   * always `low` — not because the risk is low but because there is no cut to
   * compare the probability against (D-T9.2). Showing "Riesgo bajo" there would
   * be a severity nobody measured, so the card names the origin instead
   * (D-DH.12, the owner's decision).
   */
  severity: string | null
  /** The features that carried the prediction, strongest first. */
  factors: RiskFactorLine[]
  /** The origin of the number: the version that answered, and what it is. */
  origin: string
}

/** The event's name, or `null` when this build does not know the event. */
export function eventName(eventType: string): string | null {
  return EVENT_NAMES[eventType] ?? null
}

/** The severity's word, or `null` when this build does not know the severity. */
export function severityWord(severity: string): string | null {
  return SEVERITY_WORDS[severity] ?? null
}

/**
 * The origin line: the version that answered and what it is.
 *
 * A baseline is the climatology the risk module falls back to, and it is
 * registered without calibrated cuts: the line says so, because a number
 * without its origin is a claim the producer cannot check.
 */
export function originLine(version: {
  name: string
  version: string
  is_baseline: boolean
}): string {
  const version_ = `${version.name} v${version.version}`
  return version.is_baseline
    ? `${version_} · línea base · sin cortes calibrados`
    : `${version_} · modelo calibrado`
}

/** A feature's line: its name and its value with its unit. */
export function factorLine(factor: {
  feature: string
  value: number | null
}): RiskFactorLine {
  const label = FACTOR_LABELS[factor.feature] ?? factor.feature
  if (factor.value === null) return { label, value: '—' }
  const unit = FACTOR_UNITS[factor.feature]
  const rounded = Math.round(factor.value * 100) / 100
  return { label, value: unit === undefined ? `${rounded}` : `${rounded} ${unit}` }
}

/**
 * The horizon in plain words: the first day, the last one and how many days
 * the prediction covers.
 *
 * The dates are read as UTC because they are date-only values on the wire:
 * formatting them in the phone's own zone shifts that midnight back a day on
 * any device behind UTC, and the horizon would start a day early.
 */
export function horizonLine(horizonStart: string, horizonDays: number): string {
  const start = new Date(`${horizonStart}T00:00:00Z`)
  const end = new Date(start)
  end.setUTCDate(end.getUTCDate() + horizonDays - 1)
  const day = new Intl.DateTimeFormat('es-CO', {
    day: 'numeric',
    month: 'long',
    timeZone: 'UTC',
  })
  const days = horizonDays === 1 ? '1 día' : `${horizonDays} días`
  return `${day.format(start)} → ${day.format(end)} · ${days}`
}

/**
 * One prediction, in the words the card shows.
 *
 * `null` when the event is one this build does not know: the card is left out
 * whole rather than shown with a name nobody wrote.
 */
export function riskEventLine(prediction: RiskPrediction): RiskEventLine | null {
  const event = eventName(prediction.event_type)
  if (event === null) return null
  const version = prediction.model_version
  return {
    event,
    probability: formatPercent(prediction.probability),
    horizon: horizonLine(prediction.horizon_start, prediction.horizon_days),
    severity: version.is_baseline ? null : severityWord(prediction.severity),
    factors: prediction.top_factors.map(factorLine),
    origin: originLine(version),
  }
}

/**
 * The predictions the screen shows, in the order the server sent them.
 *
 * The ones this build cannot name are dropped, not hidden: a card with a
 * missing event would claim a risk the producer cannot read.
 */
export function riskEventLines(predictions: RiskPrediction[]): RiskEventLine[] {
  return predictions
    .map(riskEventLine)
    .filter((line): line is RiskEventLine => line !== null)
}
