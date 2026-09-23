import { cn } from '../ui/utils'
import { SyncIndicator, type SyncIndicatorProps } from '../components/SyncIndicator'

/** Persistent connection-honesty strip; highlighted only while offline. */
export function OfflineBanner({ online, pendingCount, lastDataMinutesAgo, className }: SyncIndicatorProps) {
  return (
    <div className={cn('border-b border-text/10 px-4 py-2', !online && 'bg-offline/10', className)}>
      <SyncIndicator online={online} pendingCount={pendingCount} lastDataMinutesAgo={lastDataMinutesAgo} />
    </div>
  )
}
