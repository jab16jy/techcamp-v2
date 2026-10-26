import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import type { components } from '../../../lib/api/schema'

/** `crop_cycle_summary` on the wire (docs/04-api.md:235, D-T0.8). */
export type CycleSummary = components['schemas']['CropCycleSummaryView']

/**
 * The query key `queryClient.ts` documents for a persisted query: the org id
 * first, then the query name, then what identifies it — the plot.
 */
export function activeCycleSummaryKey(orgId: string | null, plotId: string | null) {
  return [orgId, 'active-cycle-summary', plotId] as const
}

/**
 * docs/04-api.md:235 — the plot's **active** cycle's impact.
 *
 * The route names no cycle id because the web cannot name one: `ActiveCycleView`
 * carries no id and there is no cycle listing (D-T9.1), so the server resolves
 * the active cycle of the plot (D-DH.13).
 */
async function fetchActiveCycleSummary(plotId: string): Promise<CycleSummary> {
  const { data, error } = await apiClient.GET('/api/v1/plots/{plot_id}/cycles/active/summary', {
    params: { path: { plot_id: plotId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /plots/{plot_id}/cycles/active/summary')
  return data
}

/**
 * The active cycle's impact, persisted so the screen opens offline with the
 * last answer the phone saw (docs/07 §Flujo de datos y offline).
 */
export function useActiveCycleSummary(orgId: string | null, plotId: string | null) {
  return useQuery({
    queryKey: activeCycleSummaryKey(orgId, plotId),
    queryFn: () => fetchActiveCycleSummary(plotId as string),
    enabled: orgId !== null && plotId !== null,
    meta: { persist: true },
  })
}
