import { cn } from '../ui/utils'
import { getStatusConfig, type StatusState } from './status'

export interface StatusBandProps {
  status: StatusState
  /** Example sentence, e.g. "Hoy: regar 12 mm ≈ 40 min". */
  message: string
  className?: string
}

/** Full-width sack-label band: color + pictogram + plain word + the day's message. */
export function StatusBand({ status, message, className }: StatusBandProps) {
  const { label, colorClass, Icon } = getStatusConfig(status)
  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-lg px-4 py-3 text-surface-raised',
        colorClass,
        className,
      )}
    >
      <Icon className="mt-0.5 size-6 shrink-0" />
      <div>
        <p className="text-lg font-semibold">{label}</p>
        <p className="text-base">{message}</p>
      </div>
    </div>
  )
}
