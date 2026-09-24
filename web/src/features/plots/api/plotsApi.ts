import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import type { components } from '../../../lib/api/schema'
import type { GeoJsonPolygon } from '../polygon'

export type FarmView = components['schemas']['FarmView']
export type PlotView = components['schemas']['PlotView']
export type IrrigationSystem = components['schemas']['IrrigationSystem']
export type GeoJsonPoint = components['schemas']['GeoJSONPoint']

export interface FarmsPage {
  farms: FarmView[]
  /** True when `GET /farms`'s `next_cursor` was non-null (#21 round 10: don't
   * silently drop farms past the first page). Paging through them is out of
   * this task's scope — T8/T9 own the fuller plots UI. */
  hasMore: boolean
}

/** docs/04-api.md: `GET /farms?org_id= → Page<Farm>`. */
async function fetchFarmsPage(orgId: string): Promise<FarmsPage> {
  const { data, error } = await apiClient.GET('/api/v1/farms', {
    params: { query: { org_id: orgId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /farms')
  return { farms: data.items, hasMore: data.next_cursor !== null }
}

/** The org's farms (first page). `orgId === null` (no org chosen yet) never fetches. */
export function useFarms(orgId: string | null) {
  return useQuery({
    queryKey: ['farms', orgId],
    queryFn: () => fetchFarmsPage(orgId as string),
    enabled: orgId !== null,
  })
}

/** docs/04-api.md: `GET /farms/{farm_id}/plots → Plot[]`. */
async function fetchPlots(farmId: string): Promise<PlotView[]> {
  const { data, error } = await apiClient.GET('/api/v1/farms/{farm_id}/plots', {
    params: { path: { farm_id: farmId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /farms/{farm_id}/plots')
  return data
}

/**
 * One query per farm (`useQueries`, not a combined `Promise.all`): a single
 * farm's plots failing to load degrades that farm only, instead of blanking
 * the whole list (#21 round 10). Returned in the same order as `farmIds`.
 */
export function usePlotsByFarm(farmIds: string[]) {
  return useQueries({
    queries: farmIds.map((farmId) => ({
      queryKey: ['plots', farmId],
      queryFn: () => fetchPlots(farmId),
    })),
  })
}

export interface CreateFarmInput {
  name: string
  municipality_code: string
  location: GeoJsonPoint
}

async function createFarm(orgId: string, input: CreateFarmInput): Promise<FarmView> {
  const { data, error } = await apiClient.POST('/api/v1/farms', {
    body: { org_id: orgId, ...input },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from POST /farms')
  return data
}

/** docs/04-api.md: `POST /farms`. Invalidates the org's farms list on success (T8). */
export function useCreateFarm(orgId: string | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateFarmInput) => createFarm(orgId as string, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['farms', orgId] })
    },
  })
}

export interface CreatePlotInput {
  name: string
  boundary: GeoJsonPolygon
  irrigation_system: IrrigationSystem
  irrigation_efficiency: number | null
  system_flow_lph: number | null
}

async function createPlot(farmId: string, input: CreatePlotInput): Promise<PlotView> {
  const { data, error } = await apiClient.POST('/api/v1/farms/{farm_id}/plots', {
    params: { path: { farm_id: farmId } },
    body: input,
  })
  if (error) throw error
  if (!data) throw new Error('empty response from POST /farms/{farm_id}/plots')
  return data
}

/** docs/04-api.md: `POST /farms/{farm_id}/plots`. Invalidates that farm's plots on success (T8). */
export function useCreatePlot(farmId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreatePlotInput) => createPlot(farmId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['plots', farmId] })
    },
  })
}
