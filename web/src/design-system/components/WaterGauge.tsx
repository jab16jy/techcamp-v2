import { cn } from '../ui/utils'

export interface WaterGaugeProps {
  /** 0-100; values outside that range are clamped. */
  percentage: number
  label: string
  /** Plain-language readout next to the bar, e.g. "62 % de capacidad". */
  valueLabel: string
  className?: string
}

/** Soil-moisture bar. Color never carries the reading alone: label and valueLabel print it. */
export function WaterGauge({ percentage, label, valueLabel, className }: WaterGaugeProps) {
  const clamped = Math.min(100, Math.max(0, percentage))
  return (
    <div className={cn('rounded-lg border border-text/10 bg-surface-raised p-4', className)}>
      <p className="text-sm text-text-muted">{label}</p>
      <div
        role="progressbar"
        aria-valuenow={clamped}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
        className="mt-2 h-3 overflow-hidden rounded-full bg-text/10"
      >
        <div className="h-full rounded-full bg-status-irrigate" style={{ width: `${clamped}%` }} />
      </div>
      <p className="mt-2 text-lg font-semibold tabular-nums">{valueLabel}</p>
    </div>
  )
}
