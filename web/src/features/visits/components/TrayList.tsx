import type { UseQueryResult } from '@tanstack/react-query'
import { Button } from '../../../design-system/ui/button'
import { ChevronDownIcon } from '../../../design-system/ui/icons'
import { cn } from '../../../design-system/ui/utils'
import type { PlotView } from '../../../lib/api/farms'
import type { TrayItem } from '../api/useTray'

const AREA_FORMAT = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 2 })

const IRRIGATION_LABELS: Record<string, string> = {
  none: 'Secano',
  drip: 'Goteo',
  sprinkler: 'Aspersión',
  gravity: 'Gravedad',
}

export interface TrayRowData {
  item: TrayItem
  isOpen: boolean
  plotsQuery?: UseQueryResult<PlotView[]>
}

export interface TrayListProps {
  rows: TrayRowData[]
  onToggleOpen: (farmId: string) => void
  onSelectPlot: (farm: TrayItem['farm'], plot: PlotView) => void
  onAddVisit: (farm: TrayItem['farm']) => void
}

/**
 * Presentational: technician tray showing assigned farms, open alert counts
 * (critical ones named), last visit date, and on-demand plots list per farm.
 * (docs/07 §Mapa de pantallas "Bandeja del técnico"; design frozen).
 */
export function TrayList({ rows, onToggleOpen, onSelectPlot, onAddVisit }: TrayListProps) {
  return (
    <div className="flex flex-col gap-6 px-4 pb-6">
      {rows.map(({ item, isOpen, plotsQuery }) => {
        const totalAlerts = item.open_alerts.length
        const criticalCount = item.open_alerts.filter((a) => a.severity === 'critical').length

        return (
          <section key={item.farm.id} aria-label={item.farm.name} className="flex flex-col">
            <div className="flex items-start justify-between gap-3">
              <button
                type="button"
                onClick={() => onToggleOpen(item.farm.id)}
                aria-expanded={isOpen}
                className="flex flex-1 flex-col items-start gap-1 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
              >
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-semibold">{item.farm.name}</h2>
                  <ChevronDownIcon
                    className={cn('size-5 text-text-muted transition-transform', isOpen && 'rotate-180')}
                  />
                </div>
                <div className="flex flex-col gap-0.5 text-sm text-text-muted">
                  <div>
                    {totalAlerts === 0 ? (
                      <span>Sin alertas abiertas</span>
                    ) : (
                      <span>
                        {totalAlerts} {totalAlerts === 1 ? 'alerta abierta' : 'alertas abiertas'}
                        {criticalCount > 0 && (
                          <span className="text-severity-critical font-medium">
                            {' '}({criticalCount} {criticalCount === 1 ? 'crítica' : 'críticas'})
                          </span>
                        )}
                      </span>
                    )}
                  </div>
                  <p>
                    Última visita: <span>{item.last_visit_on ?? 'Nunca visitada'}</span>
                  </p>
                </div>
              </button>
              <Button
                variant="secondary"
                onClick={() => onAddVisit(item.farm)}
                className="shrink-0"
              >
                Registrar visita
              </Button>
            </div>

            {isOpen && (
              <div className="mt-3">
                {plotsQuery?.isPending && (
                  <p className="text-base text-text-muted">Cargando parcelas…</p>
                )}
                {plotsQuery?.isError && (
                  <div className="flex flex-col items-start gap-2">
                    <p className="text-base text-severity-critical">
                      No se pudieron cargar las parcelas de esta finca.
                    </p>
                    <Button variant="secondary" onClick={() => void plotsQuery.refetch()}>
                      Reintentar
                    </Button>
                  </div>
                )}
                {plotsQuery?.isSuccess && plotsQuery.data.length === 0 && (
                  <p className="text-base text-text-muted">Esta finca todavía no tiene parcelas.</p>
                )}
                {plotsQuery?.isSuccess && plotsQuery.data.length > 0 && (
                  <ul className="divide-y divide-text/10 rounded-lg bg-surface-raised">
                    {plotsQuery.data.map((plot) => (
                      <li key={plot.id}>
                        <button
                          type="button"
                          onClick={() => onSelectPlot(item.farm, plot)}
                          className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
                        >
                          <span className="text-base">{plot.name}</span>
                          <span className="text-base text-text-muted">
                            {AREA_FORMAT.format(plot.area_ha)} ha ·{' '}
                            {IRRIGATION_LABELS[plot.irrigation_system] ?? plot.irrigation_system}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}
