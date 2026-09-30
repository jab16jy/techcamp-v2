/**
 * The words of the home screen's decision card (docs/07 §Mapa de pantallas,
 * Inicio item 2) and its header (item 1).
 *
 * Pure functions on purpose: docs/07 §Estructura makes the presentational layer
 * free of fetch and stores, and the sentences are the product here — the
 * server sends a `kind` and a `rationale` object (D-T0.11) and the producer
 * reads Spanish. Nothing in this file fetches, formats dates, or imports a
 * component, so every word below is asserted literally in `decisionCopy.test.ts`.
 *
 * Two rules run through all of it, both from the docs rather than from taste:
 * a missing number is an omitted clause, never a zero (docs/09's honest
 * missing-evidence rule), and a rainfed plot never shows an irrigation depth
 * nor minutes (ADR-0023).
 */
import type { components } from '../../../lib/api/schema'
import type { StatusState } from '../../../design-system/components/status'

export type ActiveCycle = NonNullable<components['schemas']['PlotStatusView']['active_cycle']>
export type Recommendation = NonNullable<components['schemas']['PlotStatusView']['recommendation']>

/** The three fields of `/status` this module reads, narrowed to what it needs. */
export interface DecisionInput {
  plot: { irrigation_system: string }
  active_cycle: ActiveCycle | null
  recommendation: Recommendation | null
}

export interface Decision {
  /** The band: color + pictogram + the one plain word (never color alone). */
  status: StatusState
  /** The day's one-line decision, e.g. "Hoy: regar 12 mm (≈ 40 min)". */
  message: string
  /** The 7-day rain the rainfed card shows, or null when there is none to show. */
  rain: string | null
  /** The day's advice, or null for a plot with an irrigation system. */
  advice: string | null
  /** The "why" behind the band, written from the stored `rationale` (D-T0.11). */
  rationale: string
}

const STAGE_KEYS = ['initial', 'development', 'mid', 'late'] as const
type StageKey = (typeof STAGE_KEYS)[number]

/** FAO-56 stage names in Spanish, as docs/00 §Etapa fenológica names them. */
const STAGE_LABELS: Record<StageKey, string> = {
  initial: 'inicial',
  development: 'desarrollo',
  mid: 'media',
  late: 'final',
}

const RECOMMENDATION_KINDS = ['irrigate', 'postpone', 'not_needed', 'no_kc', 'rainfed'] as const
type RecommendationKind = (typeof RECOMMENDATION_KINDS)[number]

const RAINFED_ADVICE = [
  'delay_sowing',
  'rain_expected',
  'conserve_moisture',
  'prioritize_harvest',
  'no_action',
] as const
type RainfedAdviceCode = (typeof RAINFED_ADVICE)[number]

/** One plain sentence per agronomic code of docs/06 §5: no jargon, no amount. */
const ADVICE_SENTENCES: Record<RainfedAdviceCode, string> = {
  delay_sowing: 'Aplaza la siembra: se espera menos lluvia que la que el cultivo necesita.',
  rain_expected: 'Espera la lluvia antes de intervenir: se espera que cubra el déficit.',
  conserve_moisture: 'Cubra el suelo con rastrojo para conservar la humedad.',
  prioritize_harvest: 'Prioriza la cosecha: el cultivo ya está en etapa final.',
  no_action: 'Por hoy no hace falta ninguna acción en esta parcela.',
}

/** No advice code at all is its own thing, and not the same as `no_action`. */
const NO_ADVICE = 'Hoy no hay consejo para esta parcela.'

const NO_RECOMMENDATION_MESSAGE = 'Aún no hay recomendación para hoy'
const NO_RECOMMENDATION_WHY =
  'La recomendación de hoy se calcula una vez al día, de madrugada. Cuando esté lista aparecerá aquí.'

const MM = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 1 })

function mm(value: number): string {
  return MM.format(value)
}

/**
 * A number the calculation actually produced.
 *
 * `rationale` is a free-form object on the wire, so anything can arrive in it:
 * a string, a null, a NaN. A value that is not a finite number is missing
 * evidence, and missing evidence has no number to print — never `0`.
 */
function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** `initial|development|mid|late` → its Spanish label, or null for a key we don't know. */
export function stageLabel(stage: string | null): string | null {
  if (stage === null) return null
  return (STAGE_KEYS as readonly string[]).includes(stage) ? STAGE_LABELS[stage as StageKey] : null
}

/**
 * Inicio item 1: "Maíz · día 42 · desarrollo".
 *
 * Each part is optional and omitted when it is missing: a crop with no Kc
 * stages has no stage, and a sowing later than today has no cycle day yet
 * (D-T0.6). The day is only shown from 1, because the sowing date is day 1 —
 * a zero or a negative day is not a cycle day, it is a bug to hide (#205).
 */
export function cycleLine(cycle: ActiveCycle | null): string | null {
  if (!cycle) return null
  const parts = [cycle.crop.name_es]
  const day = cycle.day_of_cycle
  if (typeof day === 'number' && Number.isInteger(day) && day >= 1) parts.push(`día ${day}`)
  const stage = stageLabel(cycle.stage)
  if (stage !== null) parts.push(stage)
  return parts.join(' · ')
}

/** The decision tree's `kind`, or null for a value this build does not know. */
export function readRecommendationKind(raw: string): RecommendationKind | null {
  return (RECOMMENDATION_KINDS as readonly string[]).includes(raw)
    ? (raw as RecommendationKind)
    : null
}

function readRainfedAdvice(raw: string): RainfedAdviceCode | null {
  return (RAINFED_ADVICE as readonly string[]).includes(raw) ? (raw as RainfedAdviceCode) : null
}

/**
 * The "why" behind the band, sentence by sentence (D-T0.11: the stored
 * `rationale` object, presented by the web — the server sends no second
 * presentation of it).
 *
 * Every clause is guarded by the number it needs, so a `rationale` missing a
 * field reads as a shorter explanation instead of a zero. The evidence flags
 * are shown as themselves, never hidden: a recommendation built on an
 * approximate Kc, on no sensor, on low confidence or on no forecast says so.
 */
function why(rationale: Record<string, unknown> | undefined, ...closing: string[]): string {
  const values = rationale ?? {}
  const depletion = num(values.depletion_mm)
  const raw = num(values.raw_mm)
  const et0 = num(values.et0_mm)
  const rain48 = num(values.forecast_rain_48h_mm)
  const rain7 = num(values.forecast_rain_7d_mm)
  const clauses: string[] = []

  if (depletion !== null) clauses.push(`El déficit del suelo es de ${mm(depletion)} mm.`)
  if (raw !== null) clauses.push(`El cultivo puede usar ${mm(raw)} mm de esa reserva sin estrés.`)
  if (et0 !== null) clauses.push(`La demanda de agua estimada para el día es de ${mm(et0)} mm.`)
  if (rain48 !== null) clauses.push(`Se esperan ${mm(rain48)} mm de lluvia en las próximas 48 horas.`)
  if (rain7 !== null) clauses.push(`Se esperan ${mm(rain7)} mm de lluvia en los próximos 7 días.`)
  if (values.kc_approximate === true) {
    clauses.push('El coeficiente del cultivo (Kc) es aproximado, no está calibrado para esta zona.')
  }
  if (values.without_sensor === true) {
    clauses.push('No hay sensor en la zona de raíces: el déficit se calculó solo con el modelo.')
  }
  if (values.low_confidence === true) {
    clauses.push('Esta recomendación es de baja confianza: falta evidencia de campo.')
  }
  if (values.forecast_missing === true) {
    clauses.push('No hay pronóstico disponible: la decisión se tomó sin lluvia prevista.')
  }
  if (values.missing_observed_weather === true) {
    clauses.push('Faltan datos de clima observado: la demanda se estimó con el modelo.')
  }
  clauses.push(...closing)
  return clauses.join(' ')
}

function noRecommendation(): Decision {
  return {
    status: 'watch',
    message: NO_RECOMMENDATION_MESSAGE,
    rain: null,
    advice: null,
    rationale: NO_RECOMMENDATION_WHY,
  }
}

/**
 * The rainfed card (ADR-0023): the deficit against the crop's usable reserve,
 * the rain expected in 7 days, and the day's advice. No irrigation depth, no
 * minutes — there is no system to apply them.
 *
 * The band state follows the same rule the server applies to a rainfed water
 * balance (docs/04 §Riego, ADR-0022): stress only *above* RAW, and at the limit
 * it is still `watch`, because the stress starts above RAW.
 */
function rainfedDecision(recommendation: Recommendation): Decision {
  const rationale = recommendation.rationale ?? {}
  const depletion = num(rationale.depletion_mm)
  const reserve = num(rationale.raw_mm)
  const rain7 = num(rationale.forecast_rain_7d_mm)
  const code =
    (recommendation.advice ?? []).map(readRainfedAdvice).find((advice) => advice !== null) ?? null

  let status: StatusState
  let message: string
  if (depletion === null) {
    status = 'watch'
    message = 'Hoy no hay datos suficientes para decidir'
  } else if (reserve !== null && depletion > reserve) {
    status = 'stress'
    message = `Al cultivo le faltan ${mm(depletion)} mm: está en estrés`
  } else {
    status = reserve !== null && depletion === reserve ? 'watch' : 'ok'
    message = `Al cultivo le faltan ${mm(depletion)} mm`
  }

  return {
    status,
    message,
    rain: rain7 === null ? null : `Se espera ${mm(rain7)} mm de lluvia en los próximos 7 días.`,
    advice: code === null ? NO_ADVICE : ADVICE_SENTENCES[code],
    rationale: why(rationale),
  }
}

/**
 * The day's decision, from the recommendation `/status` carries.
 *
 * A `kind` this build does not know, or an `irrigate` with no depth, is shown
 * as no recommendation at all: an unrecognized payload is not a licence to tell
 * a producer to water a field, and it is not a licence to print `0 mm` either.
 */
export function decisionFor(input: DecisionInput): Decision {
  const recommendation = input.recommendation
  const kind = recommendation ? readRecommendationKind(recommendation.kind) : null
  if (!recommendation || kind === null) return noRecommendation()

  switch (kind) {
    case 'irrigate': {
      const depth = num(recommendation.depth_mm)
      if (depth === null || depth <= 0) return noRecommendation()
      const minutes = num(recommendation.duration_min)
      // No flow rate recorded means no duration to show: the depth stands alone
      // rather than a "0 min" the plot cannot deliver.
      const message =
        minutes !== null && minutes > 0
          ? `Hoy: regar ${mm(depth)} mm (≈ ${Math.round(minutes)} min)`
          : `Hoy: regar ${mm(depth)} mm`
      return {
        status: 'irrigate',
        message,
        rain: null,
        advice: null,
        rationale: why(
          recommendation.rationale,
          'Hoy conviene regar: el déficit está por encima de la reserva del suelo.',
        ),
      }
    }
    case 'postpone':
      return {
        status: 'watch',
        message: 'Hoy: aplaza el riego, se espera lluvia',
        rain: null,
        advice: null,
        rationale: why(
          recommendation.rationale,
          'El pronóstico de 48 horas cubre el déficit: regar hoy sería tirar agua.',
        ),
      }
    case 'not_needed':
      return {
        status: 'ok',
        message: 'Hoy no necesita riego',
        rain: null,
        advice: null,
        rationale: why(
          recommendation.rationale,
          'El cultivo todavía tiene reserva en el suelo: no hace falta agua hoy.',
        ),
      }
    case 'no_kc':
      return {
        status: 'watch',
        message: 'Hoy: no hay datos del cultivo para decidir el riego',
        rain: null,
        advice: null,
        rationale: why(
          recommendation.rationale,
          'Este cultivo no tiene etapas de Kc validadas, así que no hay con qué calcular una lámina de riego.',
        ),
      }
    case 'rainfed':
      return rainfedDecision(recommendation)
  }
}
