import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import type { components } from '../../../lib/api/schema'

/** `plot_metric_monthly` on the wire (docs/04-api.md:234, D-T0.3). */
export type PlotIndicators = components['schemas']['PlotMetricMonthlyView']
/** `OrgMetricsView` (docs/04-api.md:236, D-T0.12, D-T7.1). */
export type OrgIndicators = components['schemas']['OrgMetricsView']

/** The calendar month the monthly job writes, in the one format it reads. */
export type MonthParam = string

const BOGOTA_MONTH_PARTS = { timeZone: 'America/Bogota', year: 'numeric', month: '2-digit' } as const

/**
 * The month this screen asks for: the **previous calendar month in
 * America/Bogota** (D-T9.2), the same cadence as the job that stores the rows
 * (day 1 at 02:00 computes the prior month, D-T0.7).
 *
 * Yesterday's month rather than this one because the job for the current month
 * has not run yet: asking for it would show "no data" on every first of the
 * month for a reason that has nothing to do with the plot.
 *
 * Read through `Intl` instead of `getMonth()`, because a phone on UTC-5 is up
 * to five hours into tomorrow's month at UTC midnight — on 2026-09-30T23:30Z a
 * `new Date()` in Bogotá is already October 1st, and the naive subtraction
 * would ask for August.
 */
export function previousMonthInBogota(now: Date = new Date()): MonthParam {
  const parts = new Intl.DateTimeFormat('en-US', BOGOTA_MONTH_PARTS).formatToParts(now)
  const year = Number(parts.find((part) => part.type === 'year')?.value)
  const month = Number(parts.find((part) => part.type === 'month')?.value)
  // A month of 1 steps the year back first, so January asks for the last
  // December rather than "2026-00".
  const previous = month === 1 ? year - 1 : year
  const previousMonth = month === 1 ? 12 : month - 1
  return `${previous}-${String(previousMonth).padStart(2, '0')}`
}

/**
 * The query key `queryClient.ts` documents for a persisted query: the org id
 * first, then the query name, then what identifies it — the plot and the month.
 */
export function plotIndicatorsKey(orgId: string | null, plotId: string | null, month: MonthParam) {
  return [orgId, 'plot-indicators', plotId, month] as const
}

/** docs/04-api.md:234. `404` when the job stored no row for that month. */
async function fetchPlotIndicators(plotId: string, month: MonthParam): Promise<PlotIndicators> {
  const { data, error } = await apiClient.GET('/api/v1/plots/{plot_id}/metrics', {
    params: { path: { plot_id: plotId }, query: { month } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /plots/{plot_id}/metrics')
  return data
}

/**
 * The active plot's stored adoption month, persisted to IndexedDB
 * (docs/07 §Flujo de datos y offline), so the screen opens with its last
 * report and no connection. `orgId === null` or `plotId === null` never fetches.
 */
export function usePlotIndicators(orgId: string | null, plotId: string | null, month: MonthParam) {
  return useQuery({
    queryKey: plotIndicatorsKey(orgId, plotId, month),
    queryFn: () => fetchPlotIndicators(plotId as string, month),
    enabled: orgId !== null && plotId !== null,
    meta: { persist: true },
  })
}

/** The org-first key for the organization's own month, same convention. */
export function orgIndicatorsKey(orgId: string | null, month: MonthParam) {
  return [orgId, 'org-indicators', month] as const
}

/** docs/04-api.md:236. `200` with every figure `null` for a month with no report. */
async function fetchOrgIndicators(orgId: string, month: MonthParam): Promise<OrgIndicators> {
  const { data, error } = await apiClient.GET('/api/v1/organizations/{org_id}/metrics', {
    params: { path: { org_id: orgId }, query: { month } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /organizations/{org_id}/metrics')
  return data
}

/**
 * The organization's indicators for one month, persisted like the plot's.
 * `orgId === null` never fetches, which is also how the screen keeps the read
 * from ever leaving the device for a role that may not see it: the container
 * passes `null` for `producer` and `viewer` (D-T0.10), not just when no
 * organization is chosen.
 */
export function useOrgIndicators(orgId: string | null, month: MonthParam) {
  return useQuery({
    queryKey: orgIndicatorsKey(orgId, month),
    queryFn: () => fetchOrgIndicators(orgId as string, month),
    enabled: orgId !== null,
    meta: { persist: true },
  })
}

/**
 * The `404` that means "the job stored no row for this month", which the screen
 * renders as "Sin datos este mes" rather than as a failure (docs/07:147).
 *
 * Narrow on purpose: `GET /plots/{id}/metrics` answers the **same** status for
 * a plot of another organization (docs/04-api.md:237, docs/09 §Seguridad), and
 * that one is a real error the user has to hear about — the distinction is the
 * problem's `title`, so the title is what is matched.
 */
export function isMonthWithoutReport(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false
  const problem = error as { status?: unknown; title?: unknown }
  return problem.status === 404 && problem.title === 'Plot has no metrics for that month'
}
