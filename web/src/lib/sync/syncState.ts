import { useSyncExternalStore } from 'react'
import type { SyncOutcome } from './synchronizer'

export interface SyncState {
  lastSyncedAt: string | null
  syncStopped: boolean
}

type Listener = () => void
const listeners = new Set<Listener>()

let state: SyncState = {
  lastSyncedAt: null,
  syncStopped: false,
}

export function getSyncState(): SyncState {
  return state
}

export function recordSyncOutcome(outcome: SyncOutcome): void {
  if (outcome.status === 'synced') {
    state = {
      lastSyncedAt: new Date().toISOString(),
      syncStopped: false,
    }
  } else if (outcome.status === 'stopped') {
    state = {
      ...state,
      syncStopped: true,
    }
  }
  for (const listener of listeners) listener()
}

export function subscribeSyncState(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useSyncState(): SyncState {
  return useSyncExternalStore(subscribeSyncState, getSyncState, () => ({
    lastSyncedAt: null,
    syncStopped: false,
  }))
}

export function useOnlineStatus(): boolean {
  return useSyncExternalStore(
    (callback) => {
      window.addEventListener('online', callback)
      window.addEventListener('offline', callback)
      return () => {
        window.removeEventListener('online', callback)
        window.removeEventListener('offline', callback)
      }
    },
    () => (typeof navigator !== 'undefined' ? navigator.onLine : true),
    () => true,
  )
}
