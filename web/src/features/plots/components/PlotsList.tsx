import type { UseQueryResult } from '@tanstack/react-query'
import { Button } from '../../../design-system/ui/button'
import type { FarmView, IrrigationSystem, PlotView } from '../api/plotsApi'

const IRRIGATION_LABELS: Record<IrrigationSystem, string> = {
  none: 'Secano',
  drip: 'Goteo',
  sprinkler: 'Aspersión',
  gravity: 'Gravedad',
}

const AREA_FORMAT = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 2 })

export interface FarmRow {
  farm: FarmView
  plotsQuery: UseQueryResult<PlotView[]>
}

export interface PlotsListProps {
  rows: FarmRow[]
  /** Opens the plot-creation sheet for a farm (T8). Omitted, no button renders — keeps
   * this component usable without the feature (e.g. a future read-only context). */
  onAddPlot?: (farmId: string) => void
  /** Opens the new visit sheet for a farm (E8 D14). Rendered only when technician role permits. */
  onAddVisit?: (farm: FarmView) => void
  /** Opens the plot-detail sheet — soil and crop cycle (T9). Omitted, plot rows render
   * as inert text, same reasoning as `onAddPlot`. */
  onSelectPlot?: (plot: PlotView) => void
}

/** Presentational: farms and their plots, grouped, name + area (ha) + irrigated/rainfed
 * (docs/07: irrigation system is never shown with a water-balance status color — the
 * design system's status vocabulary is reserved for water balance, not this field).
 * Each farm's plots load independently (#21 round 10): one farm's plot query failing
 * shows an inline message for that farm only, the rest of the list still renders. */
export function PlotsList({ rows, onAddPlot, onAddVisit, onSelectPlot }: PlotsListProps) {
  return (
    <div className="flex flex-col gap-6 px-4 pb-6">
      {rows.map(({ farm, plotsQuery }) => (
        <section key={farm.id}>
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-lg font-semibold">{farm.name}</h2>
            <div className="flex items-center gap-2">
              {onAddVisit && (
                <Button variant="secondary" onClick={() => onAddVisit(farm)}>
                  Registrar visita
                </Button>
              )}
              {onAddPlot && (
                <Button variant="secondary" onClick={() => onAddPlot(farm.id)}>
                  Agregar parcela
                </Button>
              )}
            </div>
          </div>
          {plotsQuery.isPending && (
            <p className="mt-2 text-base text-text-muted">Cargando parcelas…</p>
          )}
          {plotsQuery.isError && (
            <div className="mt-2 flex flex-col items-start gap-2">
              <p className="text-base text-severity-critical">
                No se pudieron cargar las parcelas de esta finca.
              </p>
              <Button variant="secondary" onClick={() => plotsQuery.refetch()}>
                Reintentar
              </Button>
            </div>
          )}
          {plotsQuery.isSuccess && plotsQuery.data.length === 0 && (
            <p className="mt-2 text-base text-text-muted">Esta finca todavía no tiene parcelas.</p>
          )}
          {plotsQuery.isSuccess && plotsQuery.data.length > 0 && (
            <ul className="mt-2 divide-y divide-text/10 rounded-lg bg-surface-raised">
              {plotsQuery.data.map((plot) => (
                <li key={plot.id}>
                  <button
                    type="button"
                    onClick={() => onSelectPlot?.(plot)}
                    className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left"
                  >
                    <span className="text-base">{plot.name}</span>
                    <span className="text-base text-text-muted">
                      {AREA_FORMAT.format(plot.area_ha)} ha · {IRRIGATION_LABELS[plot.irrigation_system]}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  )
}
