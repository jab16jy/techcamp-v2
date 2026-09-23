import { describe, expect, it } from 'vitest'
import { formatFreshness, formatSyncStatus } from './format'

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
})
