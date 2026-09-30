import { formatFreshness, formatPercent, minutesSince } from '../../../design-system/components/format'
import { MetricTile } from '../../../design-system/components/MetricTile'
import { SyncIndicator } from '../../../design-system/components/SyncIndicator'
import { AlertCard } from '../../../design-system/components/AlertCard'
import { OfflineBanner } from '../../../design-system/patterns/OfflineBanner'
import { todayInBogota } from '../../../lib/date'
import {
  NO_OPEN_ALERTS,
  alertView,
  forecastRows,
  nodeStateLabel,
  soilMoisture,
} from '../api/statusCopy'
import type { PlotStatus } from '../api/usePlotStatus'

export interface OpenAlertsProps {
  alerts: PlotStatus['open_alerts']
}

/**
 * Inicio item 3: the plot's open alerts, in the order the server sends them
 * (docs/04 §Estado: `critical` first, then newest — the home does not re-sort).
 *
 * `AlertCard` is the design system's own alert component, so severity arrives as
 * its badge: pictogram plus the plain word, never colour on its own.
 */
export function OpenAlerts({ alerts }: OpenAlertsProps) {
  if (alerts.length === 0) {
    return <p className="px-4 text-base text-text-muted">{NO_OPEN_ALERTS}</p>
  }
  return (
    <div className="flex flex-col gap-3 px-4">
      {alerts.map((alert) => (
        <AlertCard
          key={alert.id}
          severity={alertView(alert).severity}
          title={alertView(alert).title}
          description={alertView(alert).description}
          timestampLabel={formatFreshness(minutesSince(alert.opened_at))}
        />
      ))}
    </div>
  )
}

export interface SoilMoistureAndForecastProps {
  latest: PlotStatus['latest']
  waterBalance: PlotStatus['water_balance']
  weather: PlotStatus['weather_next_3d']
}

/**
 * Inicio item 4: the current soil moisture with its age, and the three forecast
 * days.
 *
 * A reading with no `at` shows "sin datos" rather than the age of some other
 * metric: the freshness belongs to the reading being shown, not to the payload.
 */
export function SoilMoistureAndForecast({
  latest,
  waterBalance,
  weather,
}: SoilMoistureAndForecastProps) {
  const reading = soilMoisture(latest, waterBalance)
  const rows = forecastRows(weather, todayInBogota())

  return (
    <div className="mt-6">
      <h2 className="px-4 text-lg font-semibold">Suelo y clima</h2>
      <div className="mt-2 rounded-lg border border-text/10 bg-surface-raised">
        <MetricTile
          label="Humedad del suelo"
          value={reading.value}
          lastDataMinutesAgo={latest.soil_moisture_pct === null ? null : minutesSince(latest.at)}
          status={reading.status}
        />
      </div>
      {rows.length > 0 && (
        <ul className="mt-2 rounded-lg border border-text/10 bg-surface-raised">
          {rows.map((row) => (
            <li
              key={row.day}
              className="flex min-h-12 items-center justify-between gap-3 border-b border-text/10 px-4 py-3 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="text-base text-text">{row.label}</p>
                {row.stale && <p className="text-sm text-text-muted">dato viejo</p>}
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <p className="whitespace-nowrap text-base tabular-nums text-text-muted">{row.rain}</p>
                <p className="whitespace-nowrap text-lg font-semibold tabular-nums">{row.temps}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export interface SyncAndNodesProps {
  nodes: PlotStatus['nodes']
  /** Minutes since the newest reading in the payload — the "last data" of the
   * connection-honesty line (docs/07). */
  lastDataMinutesAgo: number | null
  online: boolean
  pendingCount: number
  syncStopped: boolean
}

/**
 * Inicio item 5: the sync state and the plot's nodes.
 *
 * A node's state travels as a plain word, not as a status badge: `StatusBadge`
 * carries the plot's water-balance vocabulary, which is a different vocabulary
 * from a node's (DESIGN.md), so reusing it here would read as a water verdict
 * about a radio.
 */
export function SyncAndNodes({
  nodes,
  lastDataMinutesAgo,
  online,
  pendingCount,
  syncStopped,
}: SyncAndNodesProps) {
  return (
    <div className="mt-6">
      <h2 className="px-4 text-lg font-semibold">Nodos y sincronización</h2>
      <div className="mt-2 px-4">
        <SyncIndicator
          online={online}
          pendingCount={pendingCount}
          lastDataMinutesAgo={lastDataMinutesAgo}
          syncStopped={syncStopped}
        />
      </div>
      {nodes.length === 0 ? (
        <p className="px-4 text-base text-text-muted">Esta parcela no tiene nodos.</p>
      ) : (
        <ul className="mt-2 rounded-lg border border-text/10 bg-surface-raised">
          {nodes.map((node) => (
            <li
              key={node.node_id}
              className="flex min-h-12 items-center justify-between gap-3 border-b border-text/10 px-4 py-3 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="text-base text-text">{nodeStateLabel(node.status)}</p>
                <p className="text-sm text-text-muted">
                  {node.last_seen_at === null
                    ? 'Sin lectura'
                    : formatFreshness(minutesSince(node.last_seen_at))}
                </p>
              </div>
              <p className="shrink-0 whitespace-nowrap text-base tabular-nums text-text-muted">
                {formatPercent(node.completeness_24h)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export interface ConnectionBannerProps {
  online: boolean
  pendingCount: number
  lastDataMinutesAgo: number | null
  syncStopped: boolean
}

/**
 * The connection strip above the screen. It is the design system's own
 * `OfflineBanner`, so the "last data" time is the payload's own reading time:
 * with no connection the producer reads the cached state and the age of that
 * cache, which is the whole point of persisting it (D-T0.10).
 */
export function ConnectionBanner(props: ConnectionBannerProps) {
  if (props.online && !props.syncStopped) return null
  return <OfflineBanner {...props} />
}
