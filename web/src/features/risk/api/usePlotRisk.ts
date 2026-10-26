import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import type { components } from '../../../lib/api/schema'

/** `risk_prediction` on the wire (docs/04-api.md §Riesgo, métricas y asistente). */
export type RiskPrediction = components['schemas']['RiskPredictionView']

/**
 * The query key `queryClient.ts` documents for a persisted query: the org id
 * first, then the query name, then what identifies it — the plot.
 */
export function plotRiskKey(orgId: string | null, plotId: string | null) {
  return [orgId, 'plot-risk', plotId] as const
}

/**
 * docs/04-api.md §Riesgo: `GET /plots/{plot_id}/risk → RiskPrediction[]`.
 *
 * An empty list is an answer, not a failure: the plot has no prediction yet
 * (the daily job has not run for its cell, or the served version does not
 * cover it), and the screen says so in words.
 */
async function fetchPlotRisk(plotId: string): Promise<RiskPrediction[]> {
  const { data, error } = await apiClient.GET('/api/v1/plots/{plot_id}/risk', {
    params: { path: { plot_id: plotId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /plots/{plot_id}/risk')
  return data
}

/**
 * The risk of the active plot, persisted so the screen opens offline with the
 * last answer the phone saw (docs/07 §Flujo de datos y offline).
 */
export function usePlotRisk(orgId: string | null, plotId: string | null) {
  return useQuery({
    queryKey: plotRiskKey(orgId, plotId),
    queryFn: () => fetchPlotRisk(plotId as string),
    enabled: orgId !== null && plotId !== null,
    meta: { persist: true },
  })
}
