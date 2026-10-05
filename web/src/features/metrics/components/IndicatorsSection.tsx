import { formatPercent } from '../../../design-system/components/format'
import type { OrgIndicators, PlotIndicators } from '../api/indicatorsApi'

/**
 * What a figure with no evidence says, word for word (docs/07:147): "Un
 * componente `null` se muestra como 'Sin datos este mes', nunca como 0."
 *
 * Never `0`, and never a bare dash: a component without a denominator is not a
 * component that scored zero (D-T0.3, docs/11:57), and a dash hides the
 * difference between "nothing adopted" and "nothing measured".
 */
export const NO_DATA_THIS_MONTH = 'Sin datos este mes'

/**
 * The month as its own name, e.g. `septiembre de 2026`.
 *
 * `timeZone: 'UTC'` because the value is a date-only bucket: the server sends
 * the month's first day, and formatting it in the phone's own zone shifts that
 * UTC midnight back a day on any device behind UTC, turning September into
 * August. The bucket has no instant of its own, so it is read as UTC.
 */
// eslint-disable-next-line react-refresh/only-export-components -- the screen's copy vocabulary belongs with the sections that speak it
export function monthLabel(month: string): string {
  return new Intl.DateTimeFormat('es-CO', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(month),
  )
}

/** One figure: its label, and its value already formatted — or null for absent. */
interface IndicatorRow {
  label: string
  value: string | null
  unit?: string
}

/**
 * The 0–100 index as a whole number. The index splits 100 points over the
 * components that had evidence (D-T0.3), so its decimals are arithmetic rather
 * than something a farmer acts on.
 */
function indexFigure(value: number | null): string | null {
  return value === null ? null : String(Math.round(value))
}

/** A 0–1 component as a percentage, through the design system's own converter. */
function ratioFigure(ratio: number | null): string | null {
  return ratio === null ? null : formatPercent(ratio)
}

/**
 * The figures as ruled rows inside one inset grouped list — the same shape the
 * design system's `MetricTile` row lives in (a hairline-divided raised-cream
 * container, 48px minimum row height, tabular numerals on the readout).
 */
function FigureList({ rows }: { rows: IndicatorRow[] }) {
  return (
    <ul className="mx-4 divide-y divide-text/10 rounded-lg bg-surface-raised">
      {rows.map((row) => (
        <li key={row.label} className="flex min-h-12 items-center justify-between gap-3 px-4 py-3">
          <p className="min-w-0 text-base text-text">{row.label}</p>
          {row.value === null ? (
            <p className="text-base text-text-muted">{NO_DATA_THIS_MONTH}</p>
          ) : (
            <p className="whitespace-nowrap text-lg font-semibold tabular-nums">
              {row.value}
              {row.unit && (
                <span className="ml-1 text-sm font-normal text-text-muted">{row.unit}</span>
              )}
            </p>
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * One titled block: its heading, the month it speaks about, and either the
 * figures or the single absent line. `null` rows is the "the job wrote nothing
 * for this month" case, which says so once instead of repeating the sentence in
 * every row.
 */
function Section({
  id,
  title,
  month,
  rows,
}: {
  id: string
  title: string
  /** The asked month (`YYYY-MM`); the stored one wins when the row has one. */
  month: string
  rows: IndicatorRow[] | null
}) {
  return (
    <section className="mt-6" aria-labelledby={id}>
      <h2 id={id} className="px-4 text-lg font-semibold">
        {title}
      </h2>
      <p className="px-4 text-sm text-text-muted">{monthLabel(month)}</p>
      {rows === null ? (
        <p className="px-4 pt-2 text-base text-text-muted">{NO_DATA_THIS_MONTH}</p>
      ) : (
        <div className="mt-2">
          <FigureList rows={rows} />
        </div>
      )}
    </section>
  )
}

export interface PlotIndicatorsSectionProps {
  /** The month both queries asked for (`YYYY-MM`, D-T9.2). */
  month: string
  /** The active plot's stored month, or null when the job wrote none. */
  indicators: PlotIndicators | null
}

/**
 * The screen's first half: the active plot's adoption index for the month and
 * its four components (docs/07:145, docs/11 §2). Labels name what each component
 * measured rather than repeating its English identifier, because the producer
 * view carries no jargon (docs/07:14).
 *
 * The cycle summary that follows it in the doc is deferred (D-T9.1): its
 * endpoint needs a `crop_cycle_id` that `ActiveCycleView` does not expose
 * (docs/04-api.md:64) and there is no cycle listing, so this section shows the
 * index alone rather than inventing a cycle id. Nothing here needs ADR-0023's
 * rainfed variant — the figures that variant hides (water applied, irrigation
 * WUE) belong to the cycle summary, not to the components.
 *
 * No chart: docs/07 defers `TimeSeriesChart` and no chart library is installed.
 * Every figure is one ruled row, as the design system's own metric row is; the
 * difference being that a monthly aggregate carries no water-balance status
 * band, and borrowing one would put the "ok / vigilar / regar / estrés"
 * vocabulary on an adoption score (DESIGN.md, "The Two Vocabularies Rule").
 */
export function PlotIndicatorsSection({ month, indicators }: PlotIndicatorsSectionProps) {
  const rows: IndicatorRow[] | null =
    indicators === null
      ? null
      : [
          {
            label: 'Adopción digital',
            value: indexFigure(indicators.digital_adoption_index),
            unit: 'de 100',
          },
          { label: 'Medición continua', value: ratioFigure(indicators.monitoring) },
          { label: 'Registro en bitácora', value: ratioFigure(indicators.record_keeping) },
          { label: 'Decisiones con datos', value: ratioFigure(indicators.decision) },
          { label: 'Alertas con acción', value: ratioFigure(indicators.risk_management) },
        ]
  return (
    <Section
      id="indicadores-parcela"
      title="Parcela activa"
      month={indicators?.month ?? month}
      rows={rows}
    />
  )
}

export interface OrgIndicatorsSectionProps {
  /** The month both queries asked for (`YYYY-MM`, D-T9.2). */
  month: string
  /** The organization's month, or null when it has nothing to report. */
  indicators: OrgIndicators | null
}

/**
 * The organization's own figures for the same month (docs/07:148), labelled
 * with docs/11 §2's own names for them.
 *
 * `harvested_cycles_ratio` and `median_hours_to_first_reading` are `null` in this
 * lane (D-T7.1): the org-month listing carries neither cycles nor node instants,
 * so they read as absent until the follow-up lane adds their views. `0` would say
 * "no cycle was harvested" and "every node answered instantly".
 */
export function OrgIndicatorsSection({ month, indicators }: OrgIndicatorsSectionProps) {
  const rows: IndicatorRow[] | null =
    indicators === null
      ? null
      : [
          {
            label: 'Adopción digital promedio',
            value: indexFigure(indicators.mean_digital_adoption_index),
            unit: 'de 100',
          },
          {
            label: 'Parcelas con índice',
            value: indicators.plots_with_index === null ? null : String(indicators.plots_with_index),
          },
          { label: 'Parcelas monitoreadas', value: ratioFigure(indicators.monitored_plots_ratio) },
          { label: 'Ciclos cerrados con cosecha', value: ratioFigure(indicators.harvested_cycles_ratio) },
          {
            label: 'Tiempo a primera lectura',
            value:
              indicators.median_hours_to_first_reading === null
                ? null
                : String(indicators.median_hours_to_first_reading),
            unit: 'h',
          },
        ]
  return (
    <Section
      id="indicadores-organizacion"
      title="Organización"
      month={indicators?.month ?? month}
      rows={rows}
    />
  )
}
