import { cn } from '../ui/utils'
import { formatSyncStatus, type SyncStatusInput } from './format'

export interface SyncIndicatorProps extends SyncStatusInput {
  className?: string
}

/** Connection honesty, printed as one text line: never a chrome badge. */
export function SyncIndicator({ online, pendingCount, lastDataMinutesAgo, className }: SyncIndicatorProps) {
  return (
    <p className={cn('text-sm', online ? 'text-text-muted' : 'text-offline', className)}>
      {formatSyncStatus({ online, pendingCount, lastDataMinutesAgo })}
    </p>
  )
}
