import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import type { components } from '../../../lib/api/schema'

/**
 * docs/04 §Visitas de extensión y bandeja del técnico: `GET /me/tray`.
 * Assigned farms to the technician across all memberships in D-T0.8 order.
 */
export type TrayItem = components['schemas']['TrayItemView']

/**
 * The query key `queryClient.ts` documents for a persisted query: the org id
 * first, so switching organizations can never read another one's tray data
 * out of the phone's cache, then the query name.
 */
export function trayKey(orgId: string | null) {
  return [orgId, 'tray'] as const
}

/** docs/04: `GET /api/v1/me/tray`. */
async function fetchTray(): Promise<TrayItem[]> {
  const { data, error } = await apiClient.GET('/api/v1/me/tray', {})
  if (error) throw error
  if (!data) throw new Error('empty response from /me/tray')
  return data
}

/**
 * The technician's tray, persisted to IndexedDB (docs/07 §Flujo de datos y
 * offline: "offline se ve el último estado con su hora"), so the screen opens
 * without a connection. `orgId === null` never fetches.
 */
export function useTray(orgId: string | null) {
  return useQuery({
    queryKey: trayKey(orgId),
    queryFn: fetchTray,
    enabled: orgId !== null,
    meta: { persist: true },
  })
}
