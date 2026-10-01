import { type QueryClient, useQuery } from '@tanstack/react-query'
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

/**
 * Puts the tray already on the phone under the key of the org the user just switched
 * to (D-T6.1), stamped with the hour the server actually answered.
 *
 * `GET /me/tray` returns the assigned farms of EVERY org the technician belongs to, so
 * the data is the same; only the KEY changes, because docs/07 keeps the organization in
 * it ("las claves de consulta llevan la organización") and the key is what stops one
 * organization's data being read as another's. Without this seeding, the way back to the
 * tray reads `['org-2', 'tray']` — never fetched, never persisted — and sits on
 * "Cargando bandeja…" with no connection.
 *
 * Persistence: the persister writes what `persistOptions.dehydrateOptions` selects, and
 * that predicate reads `meta.persist` off the Query's options. A query built by
 * `setQueryData` takes those options from the defaults registered for its key (v5
 * `defaultQueryOptions({ queryKey })`), so registering the same opt-in here is what puts
 * the seeded entry on the phone exactly like a fetched one.
 *
 * `items === null` seeds nothing: a tray the phone never received stays absent instead
 * of becoming a cached "no farms".
 */
export function seedTrayForOrg(
  client: QueryClient,
  orgId: string,
  items: TrayItem[] | null,
  dataUpdatedAt: number,
): void {
  if (items === null) return
  const key = trayKey(orgId)
  client.setQueryDefaults(key, { meta: { persist: true } })
  client.setQueryData(key, items, { updatedAt: dataUpdatedAt })
}
