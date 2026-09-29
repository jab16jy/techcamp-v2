import { cn } from '../ui/utils'
import { formatSyncStatus, type SyncStatusInput } from './format'

export interface SyncIndicatorProps extends SyncStatusInput {
  className?: string
}

/** Connection honesty, printed as one text line: never a chrome badge. */
export function SyncIndicator({ online, pendingCount, lastDataMinutesAgo, syncStopped, className }: SyncIndicatorProps) {
  return (
    <p className={cn('text-sm', (!online || syncStopped) ? 'text-offline' : 'text-text-muted', className)}>
      {formatSyncStatus({ online, pendingCount, lastDataMinutesAgo, syncStopped })}
    </p>
  )
}
