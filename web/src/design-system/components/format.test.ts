import { describe, expect, it } from 'vitest'
import { formatFreshness, formatPercent, formatSyncStatus, minutesSince } from './format'

describe('formatFreshness', () => {
  it('reports no data when minutesAgo is null', () => {
    expect(formatFreshness(null)).toBe('sin datos')
  })

  it('reports instants for less than a minute', () => {
    expect(formatFreshness(0)).toBe('dato de hace instantes')
  })

  it('uses singular minute wording for exactly 1', () => {
    expect(formatFreshness(1)).toBe('dato de hace 1 min')
  })

  it('reports the minute count otherwise', () => {
    expect(formatFreshness(12)).toBe('dato de hace 12 min')
  })
})

describe('formatSyncStatus', () => {
  it('joins offline, pending and freshness clauses, matching the contract example', () => {
    expect(
      formatSyncStatus({ online: false, pendingCount: 3, lastDataMinutesAgo: 12 }),
    ).toBe('Sin conexión · 3 por subir · dato de hace 12 min')
  })

  it('omits the offline clause when online', () => {
    expect(formatSyncStatus({ online: true, pendingCount: 0, lastDataMinutesAgo: 2 })).toBe(
      'dato de hace 2 min',
    )
  })

  it('omits the pending clause when nothing is queued', () => {
    expect(
      formatSyncStatus({ online: false, pendingCount: 0, lastDataMinutesAgo: 0 }),
    ).toBe('Sin conexión · dato de hace instantes')
  })

  it('includes the pending clause when online with a queue', () => {
    expect(
      formatSyncStatus({ online: true, pendingCount: 1, lastDataMinutesAgo: null }),
    ).toBe('1 por subir · sin datos')
  })

  it('includes sincronización detenida when sync is stopped', () => {
    expect(
      formatSyncStatus({ online: true, pendingCount: 1, lastDataMinutesAgo: 5, syncStopped: true }),
    ).toBe('sincronización detenida · 1 por subir · dato de hace 5 min')
  })
})

describe('minutesSince', () => {
  it('reports whole minutes since an ISO timestamp', () => {
    const twelveMinutesAgo = new Date(Date.now() - 12 * 60_000).toISOString()
    expect(minutesSince(twelveMinutesAgo)).toBe(12)
  })

  it('reports no minutes for a missing or unparsable timestamp', () => {
    expect(minutesSince(null)).toBeNull()
    expect(minutesSince('not-a-date')).toBeNull()
  })

  it('never goes negative for a clock ahead of ours', () => {
    expect(minutesSince(new Date(Date.now() + 60_000).toISOString())).toBe(0)
  })
})

describe('formatPercent', () => {
  it('shows a 0-1 ratio as a percentage with one decimal', () => {
    expect(formatPercent(0.198)).toBe('19.8 %')
  })

  it('rounds a long float instead of printing it raw', () => {
    expect(formatPercent(0.19791666666666666)).toBe('19.8 %')
  })

  it('keeps a whole percentage whole', () => {
    expect(formatPercent(1)).toBe('100.0 %')
  })

  it('reports a bare dash, with no unit, for a missing ratio', () => {
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(undefined)).toBe('—')
  })
})
