import { cn } from '../ui/utils'
import { formatFreshness } from './format'

export interface MetricTileProps {
  label: string
  value: string | number
  unit?: string
  /** Minutes since this reading, or null when there is no data yet. */
  lastDataMinutesAgo: number | null
  className?: string
}

/** A single field measurement: value, unit and when it was last seen (connection honesty). */
export function MetricTile({ label, value, unit, lastDataMinutesAgo, className }: MetricTileProps) {
  return (
    <div className={cn('rounded-lg border border-text/10 bg-surface-raised p-4', className)}>
      <p className="text-sm text-text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">
        {value}
        {unit && <span className="ml-1 text-lg font-normal text-text-muted">{unit}</span>}
      </p>
      <p className="mt-1 text-sm text-text-muted">{formatFreshness(lastDataMinutesAgo)}</p>
    </div>
  )
}
