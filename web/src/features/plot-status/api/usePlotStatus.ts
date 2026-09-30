import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import type { components } from '../../../lib/api/schema'

/**
 * The whole home screen in one request (docs/04 §Estado de la parcela, D-T0.1):
 * one call instead of six on a 3G phone.
 */
export type PlotStatus = components['schemas']['PlotStatusView']

/**
 * The query key `queryClient.ts` documents for a persisted query: the org id
 * first, so switching organizations can never read another one's plot status
 * out of the phone's cache, then the query name, then what identifies it.
 */
export function plotStatusKey(orgId: string | null, plotId: string | null) {
  return [orgId, 'status', plotId] as const
}

/** docs/04: `GET /plots/{plot_id}/status`. 404 for a plot of another organization. */
async function fetchPlotStatus(plotId: string): Promise<PlotStatus> {
  const { data, error } = await apiClient.GET('/api/v1/plots/{plot_id}/status', {
    params: { path: { plot_id: plotId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /plots/{plot_id}/status')
  return data
}

/**
 * The active plot's status, persisted to IndexedDB (docs/07 §Flujo de datos y
 * offline: "offline se ve el último estado con su hora"), so the screen opens
 * without a connection. `orgId === null` or `plotId === null` never fetches.
 */
export function usePlotStatus(orgId: string | null, plotId: string | null) {
  return useQuery({
    queryKey: plotStatusKey(orgId, plotId),
    queryFn: () => fetchPlotStatus(plotId as string),
    enabled: orgId !== null && plotId !== null,
    meta: { persist: true },
  })
}
