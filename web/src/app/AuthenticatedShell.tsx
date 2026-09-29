import { useEffect, useRef } from 'react'
import { AppShell } from '../design-system/patterns/AppShell'
import { useToken } from '../lib/api/session'
import { resetSyncState } from '../lib/sync/syncState'

/**
 * Top-level authenticated shell layout in `app/`.
 * ADR-0005: calls `requestPersistentStorage()` once on app start.
 * Starts the synchronizer once per signed-in session and stops it on sign-out.
 * Dynamic imports keep Dexie out of the initial bundle (RNF-02 ≤ 200 kB).
 */
export function AuthenticatedShell() {
  const token = useToken()
  const stopRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    void import('../lib/db/db').then((m) => {
      m.requestPersistentStorage()
    })
  }, [])

  useEffect(() => {
    if (!token) return
    let active = true

    void import('../lib/sync/triggers').then((m) => {
      if (!active) return
      const stop = m.startSynchronizer()
      stopRef.current = stop
    })

    return () => {
      active = false
      stopRef.current?.()
      stopRef.current = null
      resetSyncState()
    }
  }, [token])

  return <AppShell />
}
