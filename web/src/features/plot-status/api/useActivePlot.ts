import { useMemo } from 'react'
import { useActivePlotId } from '../../../lib/api/activePlot'
import { useFarms, usePlotsByFarm, type PlotView } from '../../../lib/api/farms'
import { useOrgId } from '../../../lib/api/session'

/**
 * Which plot the home screen is about (D-T0.7): the last one the user opened in
 * this organization on this device, and otherwise the first plot of the first
 * farm, in API order. A remembered plot the organization no longer has is
 * dropped rather than requested — the fallback is the same first plot, so the
 * home screen always shows a plot the user can actually see.
 *
 * The resolution is a state, not a nullable plot, because "no organization",
 * "still loading", "failed" and "this organization has no plots" are four
 * different things on screen and each of them has its own honest copy.
 */
export type ActivePlotState =
  | { kind: 'no-org' }
  | { kind: 'loading' }
  | { kind: 'error'; error: unknown; retry: () => void }
  | { kind: 'no-plots' }
  | { kind: 'ready'; plot: PlotView }

export function useActivePlot(): ActivePlotState {
  const orgId = useOrgId()
  const rememberedPlotId = useActivePlotId(orgId)
  const farmsQuery = useFarms(orgId)
  const farmIds = useMemo(
    () => farmsQuery.data?.farms.map((farm) => farm.id) ?? [],
    [farmsQuery.data],
  )
  const plotsQueries = usePlotsByFarm(farmIds)

  const retry = () => {
    void farmsQuery.refetch()
    for (const query of plotsQueries) void query.refetch()
  }

  if (orgId === null) return { kind: 'no-org' }
  if (farmsQuery.isError) return { kind: 'error', error: farmsQuery.error, retry }
  if (plotsQueries.some((query) => query.isError)) {
    const failed = plotsQueries.find((query) => query.isError)
    return { kind: 'error', error: failed?.error, retry }
  }
  if (!farmsQuery.isSuccess || plotsQueries.some((query) => query.isPending)) {
    return { kind: 'loading' }
  }

  // Farms in API order, each farm's plots in API order: the documented default.
  const plots = farmIds.flatMap((_, index) => plotsQueries[index]?.data ?? [])
  if (plots.length === 0) return { kind: 'no-plots' }

  const remembered = plots.find((plot) => plot.id === rememberedPlotId)
  return { kind: 'ready', plot: remembered ?? plots[0] }
}
