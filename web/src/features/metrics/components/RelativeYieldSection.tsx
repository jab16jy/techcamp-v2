import type { CycleSummary } from '../api/cycleSummaryApi'

/**
 * The relative yield, and what it says when there is none.
 *
 * `relative_yield` is the plot's yield against the **municipality's** median for
 * the same crop (docs/11:23). It is `null` until the table with the municipal
 * field data exists: the EVA data of the v1 was not recovered (D-T0.9), and the
 * table it needs is the E17 epic's work.
 *
 * So the line shows a dash and says why, in words the producer can act on.
 * Never `0`: zero would say the plot yields nothing relative to its
 * municipality, which nobody measured (D-T0.3).
 */
export const NO_MUNICIPAL_REFERENCE =
  'Todavía no hay datos del municipio para comparar: el dato de campo municipal no existe aún.'

/** The dash for a figure that has no value to show (D-DH.11). */
export const NO_VALUE = '—'

export interface RelativeYieldSectionProps {
  /** The active cycle's summary, or null when there is no active cycle. */
  summary: CycleSummary | null
}

/**
 * The screen's cycle half, with the one figure this lane can answer: the
 * relative yield against the municipality (D-DH.11).
 *
 * The rest of the cycle summary (docs/07:146) stays deferred: the endpoint that
 * answers it needs a cycle the web cannot name, and naming one is a separate
 * server task (D-T9.1). The relative yield is here because the server resolves
 * the active cycle for this read (D-DH.13), so the line can be honest about
 * what it does not have.
 */
export function RelativeYieldSection({ summary }: RelativeYieldSectionProps) {
  if (summary === null) return null
  /**
   * `undefined` is treated as `null`: a persisted answer from a build older
   * than this line restores without the field at all, and a field that is not
   * there is the absence this section shows, not a figure (D-T0.3).
   */
  const relativeYield = summary.relative_yield ?? null
  return (
    <section className="mt-6" aria-labelledby="indicadores-ciclo">
      <h2 id="indicadores-ciclo" className="px-4 text-lg font-semibold">
        Ciclo actual
      </h2>
      <ul className="mx-4 mt-2 divide-y divide-text/10 rounded-lg bg-surface-raised">
        <li className="px-4 py-3">
          <div className="flex min-h-12 items-center justify-between gap-3">
            <p className="min-w-0 text-base text-text">Rendimiento relativo municipal</p>
            <p className="whitespace-nowrap text-lg font-semibold tabular-nums">
              {relativeYield === null ? NO_VALUE : relativeYield.toFixed(2)}
            </p>
          </div>
          {relativeYield === null && (
            <p className="mt-1 text-sm text-text-muted">{NO_MUNICIPAL_REFERENCE}</p>
          )}
        </li>
      </ul>
    </section>
  )
}
