/** Freshness clause, shared by MetricTile and SyncIndicator: "connection honesty" (docs/07). */
export function formatFreshness(minutesAgo: number | null): string {
  if (minutesAgo === null) return 'sin datos'
  if (minutesAgo < 1) return 'dato de hace instantes'
  if (minutesAgo === 1) return 'dato de hace 1 min'
  return `dato de hace ${minutesAgo} min`
}

export interface SyncStatusInput {
  online: boolean
  pendingCount: number
  lastDataMinutesAgo: number | null
}

/**
 * The connection-honesty text line, e.g. "Sin conexión · 3 por subir · dato de hace 12 min".
 * Silence on the offline clause means online; the pending clause only appears with a queue.
 */
export function formatSyncStatus({ online, pendingCount, lastDataMinutesAgo }: SyncStatusInput): string {
  const clauses: string[] = []
  if (!online) clauses.push('Sin conexión')
  if (pendingCount > 0) clauses.push(`${pendingCount} por subir`)
  clauses.push(formatFreshness(lastDataMinutesAgo))
  return clauses.join(' · ')
}

/** Whole minutes since an ISO timestamp, for `formatFreshness`'s input. A clock ahead of
 * ours reads as 0, never as a negative age. */
export function minutesSince(iso: string | null): number | null {
  if (iso === null) return null
  const elapsed = Date.now() - Date.parse(iso)
  if (Number.isNaN(elapsed)) return null
  return Math.max(0, Math.round(elapsed / 60_000))
}
