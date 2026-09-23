import { cn } from '../ui/utils'
import { getStatusConfig, type StatusState } from './status'

export interface StatusBadgeProps {
  status: StatusState
  className?: string
}

/** Compact pill for lists and cards: same word and pictogram as StatusBand, no message. */
export function StatusBadge({ status, className }: StatusBadgeProps) {
  const { label, colorClass, Icon } = getStatusConfig(status)
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium text-surface-raised',
        colorClass,
        className,
      )}
    >
      <Icon className="size-4" />
      {label}
    </span>
  )
}
