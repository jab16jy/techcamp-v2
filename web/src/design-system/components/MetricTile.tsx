import { cn } from '../ui/utils'
import { formatFreshness } from './format'
import { StatusBadge } from './StatusBadge'
import type { StatusState } from './status'

export interface MetricTileProps {
  label: string
  value: string | number
  unit?: string
  /** Minutes since this reading, or null when there is no data yet. */
  lastDataMinutesAgo: number | null
  /** This reading's status classification — same word/pictogram as StatusBand. */
  status: StatusState
  className?: string
}

/**
 * A single field measurement, as one hairline-ruled row inside an inset grouped
 * list: value, unit and freshness (connection honesty), ending in the reading's
 * own status band — never a floating hero-metric card.
 */
export function MetricTile({ label, value, unit, lastDataMinutesAgo, status, className }: MetricTileProps) {
  return (
    <div className={cn('flex min-h-12 items-center justify-between gap-3 px-4 py-3', className)}>
      <div className="min-w-0">
        <p className="text-base text-text">{label}</p>
        <p className="text-sm text-text-muted">{formatFreshness(lastDataMinutesAgo)}</p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <p className="whitespace-nowrap text-lg font-semibold tabular-nums">
          {value}
          {unit && <span className="ml-1 text-sm font-normal text-text-muted">{unit}</span>}
        </p>
        <StatusBadge status={status} />
      </div>
    </div>
  )
}
