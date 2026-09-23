import { cn } from '../ui/utils'
import { StatusBadge } from './StatusBadge'
import type { StatusState } from './status'

export interface WaterGaugeProps {
  /** 0-100; values outside that range are clamped. */
  percentage: number
  label: string
  /** Plain-language readout next to the bar, e.g. "62 % de capacidad". */
  valueLabel: string
  /** This reading's status classification — same word/pictogram as StatusBand. */
  status: StatusState
  className?: string
}

/**
 * Soil-moisture row inside an inset grouped list: color never carries the
 * reading alone — label, tabular value and the status band print it.
 */
export function WaterGauge({ percentage, label, valueLabel, status, className }: WaterGaugeProps) {
  const clamped = Math.min(100, Math.max(0, percentage))
  return (
    <div className={cn('flex flex-col gap-2 px-4 py-3', className)}>
      <div className="flex items-center justify-between gap-3">
        <p className="text-base text-text">{label}</p>
        <div className="flex shrink-0 items-center gap-3">
          <p className="whitespace-nowrap text-lg font-semibold tabular-nums">{valueLabel}</p>
          <StatusBadge status={status} />
        </div>
      </div>
      <div
        role="progressbar"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className="h-3 overflow-hidden rounded-full bg-text/10"
      >
        <div className="h-full rounded-full bg-status-irrigate" style={{ width: `${clamped}%` }} />
      </div>
    </div>
  )
}
