import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  getSyncState,
  recordSyncOutcome,
  resetSyncState,
} from './syncState'

describe('syncState: recordSyncOutcome and resetSyncState', () => {
  beforeEach(() => {
    resetSyncState()
  })

  afterEach(() => {
    resetSyncState()
  })

  it('records synced: sets lastSyncedAt and clears syncStopped', () => {
    recordSyncOutcome({ status: 'synced', pushed: 1, pulled: 2 })

    const state = getSyncState()
    expect(state.syncStopped).toBe(false)
    expect(state.lastSyncedAt).not.toBeNull()
    expect(typeof state.lastSyncedAt).toBe('string')
  })

  it('records stopped with reason unavailable: syncStopped remains false and lastSyncedAt is preserved', () => {
    recordSyncOutcome({ status: 'synced', pushed: 0, pulled: 0 })
    const lastSync = getSyncState().lastSyncedAt

    recordSyncOutcome({ status: 'stopped', reason: 'unavailable' })

    const state = getSyncState()
    expect(state.syncStopped).toBe(false)
    expect(state.lastSyncedAt).toBe(lastSync)
  })

  it('records stopped with reason unauthorized: syncStopped becomes true', () => {
    recordSyncOutcome({ status: 'synced', pushed: 0, pulled: 0 })
    const lastSync = getSyncState().lastSyncedAt

    recordSyncOutcome({ status: 'stopped', reason: 'unauthorized' })

    const state = getSyncState()
    expect(state.syncStopped).toBe(true)
    expect(state.lastSyncedAt).toBe(lastSync)
  })

  it('records stopped with unknown entity: syncStopped becomes true', () => {
    recordSyncOutcome({ status: 'synced', pushed: 0, pulled: 0 })

    recordSyncOutcome({ status: 'stopped', reason: 'unknown_entity' })

    const state = getSyncState()
    expect(state.syncStopped).toBe(true)
  })

  it('a later synced clears syncStopped and updates lastSyncedAt', () => {
    recordSyncOutcome({ status: 'stopped', reason: 'unauthorized' })
    expect(getSyncState().syncStopped).toBe(true)

    recordSyncOutcome({ status: 'synced', pushed: 3, pulled: 0 })

    const state = getSyncState()
    expect(state.syncStopped).toBe(false)
    expect(state.lastSyncedAt).not.toBeNull()
  })

  it('resetSyncState clears lastSyncedAt and syncStopped to initial state', () => {
    recordSyncOutcome({ status: 'synced', pushed: 1, pulled: 0 })
    recordSyncOutcome({ status: 'stopped', reason: 'unauthorized' })

    expect(getSyncState().syncStopped).toBe(true)
    expect(getSyncState().lastSyncedAt).not.toBeNull()

    resetSyncState()

    expect(getSyncState()).toEqual({
      lastSyncedAt: null,
      syncStopped: false,
    })
  })
})
