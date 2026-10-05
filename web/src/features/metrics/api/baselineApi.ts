import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import type { components } from '../../../lib/api/schema'

/** The enrollment survey as the wire types it (docs/04-api.md:233, ADR-0024). */
export type PlotBaselineView = components['schemas']['PlotBaselineView']
export type PlotBaselineInput = components['schemas']['PlotBaselineInput']
/** The closed vocabulary of `metrics.domain.models.IrrigationPractice` (server, T2). */
export type IrrigationPractice = components['schemas']['IrrigationPractice']

/**
 * The four practices in the same words `PlotsList` already uses for
 * `plot.irrigation_system`: the survey compares like with like, so a producer
 * reading one vocabulary on the plot list and another on the form would think
 * they were two different questions.
 *
 * They live beside `IrrigationPractice` rather than in the form because
 * `react-refresh` only allows a component file to export components, and the
 * repo's own precedent (`features/nodes/components/calibrationLabels.ts`) puts
 * such a list in its own module — which this lane's surfaces do not include.
 */
export const PRACTICE_LABELS: Record<IrrigationPractice, string> = {
  none: 'Secano',
  drip: 'Goteo',
  sprinkler: 'Aspersión',
  gravity: 'Gravedad',
}

/** The Spanish word for one practice, falling back to the wire value itself so
 * an unexpected value from the server is shown, never hidden. */
export function practiceLabel(value: string): string {
  return PRACTICE_LABELS[value as IrrigationPractice] ?? value
}

/**
 * The query key `queryClient.ts` documents for a persisted query: the org id
 * first, so switching organizations can never read another one's survey out of
 * the phone's cache, then the query name, then the plot it belongs to.
 */
export function baselineQueryKey(orgId: string | null, plotId: string) {
  return [orgId, 'baseline', plotId] as const
}

/** docs/04-api.md: `GET /plots/{plot_id}/baseline`. Any member reads it; a plot
 * without a survey answers `404`, which the section turns into an invitation to
 * register one rather than an error to apologize for. */
async function fetchBaseline(plotId: string): Promise<PlotBaselineView> {
  const { data, error } = await apiClient.GET('/api/v1/plots/{plot_id}/baseline', {
    params: { path: { plot_id: plotId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from GET /plots/{plot_id}/baseline')
  return data
}

/**
 * The plot's enrollment survey, persisted to IndexedDB like the plot status
 * (docs/07 §Flujo de datos y offline: "offline se ve el último estado con su
 * hora"), so a farmer in the field reads the recorded figures without a
 * connection. `orgId === null` never fetches.
 */
export function usePlotBaseline(orgId: string | null, plotId: string) {
  return useQuery({
    queryKey: baselineQueryKey(orgId, plotId),
    queryFn: () => fetchBaseline(plotId),
    enabled: orgId !== null,
    meta: { persist: true },
  })
}

async function putBaseline(plotId: string, input: PlotBaselineInput): Promise<PlotBaselineView> {
  const { data, error } = await apiClient.PUT('/api/v1/plots/{plot_id}/baseline', {
    params: { path: { plot_id: plotId } },
    body: input,
  })
  if (error) throw error
  if (!data) throw new Error('empty response from PUT /plots/{plot_id}/baseline')
  return data
}

/**
 * docs/04-api.md: `PUT /plots/{plot_id}/baseline` creates or replaces the whole
 * survey for an owner or technician (`403` otherwise, `422` for a `crop_id`
 * outside the catalog). Online only: `queryClient.ts` never persists a mutation
 * and the outbox in `db.ts` is the logbook's, so this PUT needs a connection
 * like the soil PUT does.
 *
 * The response is the stored row, so `onSuccess` writes it under the same key
 * `usePlotBaseline` reads: the form's save shows the saved survey with no
 * second request.
 */
export function usePutBaseline(orgId: string | null, plotId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: PlotBaselineInput) => putBaseline(plotId, input),
    onSuccess: (baseline) => queryClient.setQueryData(baselineQueryKey(orgId, plotId), baseline),
  })
}