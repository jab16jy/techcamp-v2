import { useEffect, useMemo } from 'react'
import { forgetActivePlotId, setActivePlotId, useActivePlotId } from '../../../lib/api/activePlot'
import { useFarms, usePlotsByFarm, type PlotView } from '../../../lib/api/farms'
import { useOrgId } from '../../../lib/api/session'

/**
 * Which plot the home screen is about (D-T0.7): the last one the user opened in
 * this organization on this device, and otherwise the first plot of the first
 * farm, in API order.
 *
 * ## A remembered plot is used without waiting for the lists (D-T0.10)
 *
 * The farm and plot lists are **not** persisted — their query keys are not
 * org-first, so `queryClient` refuses to write them to the phone. That makes
 * them useless offline, and a resolution that waits for them leaves the home
 * screen on "Cargando parcelas…" forever with no connection: the persisted
 * `/status` it was about to show never gets read. So a remembered plot is
 * returned straight away, and the lists only decide when there is nothing
 * remembered, or when they prove the remembered plot is gone.
 *
 * @see `PlotStatusScreen`, which forgets a remembered plot the server answers
 * 404 for — the one case the lists cannot see.
 */
export type ActivePlotState =
  | { kind: 'no-org' }
  | { kind: 'loading' }
  | { kind: 'error'; error: unknown; retry: () => void }
  | { kind: 'no-plots' }
  | { kind: 'ready'; plotId: string; plotName: string | null }

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

  const listsFailed = farmsQuery.isError || plotsQueries.some((query) => query.isError)
  const listsSettled =
    farmsQuery.isSuccess && !listsFailed && !plotsQueries.some((query) => query.isPending)

  // Farms in API order, each farm's plots in API order: the documented default.
  const plots: PlotView[] = listsSettled
    ? farmIds.flatMap((_, index) => plotsQueries[index]?.data ?? [])
    : []
  const remembered = rememberedPlotId === null ? undefined : plots.find((p) => p.id === rememberedPlotId)
  const defaultPlot = plots[0]

  // A remembered plot answers while the lists are still loading, and keeps
  // answering once they arrive containing it.
  const usingRemembered = rememberedPlotId !== null && (!listsSettled || remembered !== undefined)
  const resolvedPlotId = usingRemembered ? rememberedPlotId : defaultPlot?.id
  const resolvedPlotName = usingRemembered ? (remembered?.name ?? null) : (defaultPlot?.name ?? null)
  // Keyed on primitives so the object is stable across renders: a fresh object
  // here would re-run the remembering effect on every render of the screen.
  const resolved = useMemo(
    () =>
      resolvedPlotId === undefined ? undefined : { plotId: resolvedPlotId, plotName: resolvedPlotName },
    [resolvedPlotId, resolvedPlotName],
  )

  /**
   * Remember the plot the home resolved, so the next open has one to ask for
   * before any list can load (D-T0.10: the lists themselves are not persisted).
   *
   * Only the *default* is ever written: a plot the user opened is already
   * remembered, and writing the default over it would silently move the home
   * screen back to the first farm's first plot on the next open. A remembered
   * plot the lists prove is gone is replaced here, which is the same write.
   */
  useEffect(() => {
    if (orgId === null || resolved === undefined || usingRemembered) return
    if (resolved.plotId === rememberedPlotId) return
    setActivePlotId(orgId, resolved.plotId)
  }, [orgId, resolved, rememberedPlotId, usingRemembered])

  if (orgId === null) return { kind: 'no-org' }
  if (resolved !== undefined) return { kind: 'ready', ...resolved }
  if (listsFailed) {
    const failed = plotsQueries.find((query) => query.isError)
    return { kind: 'error', error: farmsQuery.isError ? farmsQuery.error : failed?.error, retry }
  }
  if (!listsSettled) return { kind: 'loading' }
  // The lists answered and the organization has no plot to show.
  return { kind: 'no-plots' }
}

/** The 404 a `/status` answers for a plot the caller may not see (docs/09). */
export function isPlotGone(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false
  const problem = error as { status?: unknown; title?: unknown }
  return problem.status === 404 || problem.title === 'Plot not found'
}

/**
 * Drops a remembered plot the server no longer knows about, so the next render
 * resolves the organization's default instead of asking for a plot that is gone
 * (a plot deleted, or one the user no longer has access to).
 */
export function useForgetMissingPlot(orgId: string | null, plotId: string | null, error: unknown): void {
  useEffect(() => {
    if (orgId === null || plotId === null) return
    if (!isPlotGone(error)) return
    forgetActivePlotId(orgId)
  }, [orgId, plotId, error])
}
