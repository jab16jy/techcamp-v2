import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../../lib/api/client'
import { useFarms, usePlotsByFarm, type FarmView, type PlotView } from '../../../lib/api/farms'
import type { components } from '../../../lib/api/schema'
import type { GeoJsonPolygon } from '../polygon'

// The farm and plot list queries moved to `lib/api/farms.ts` in E9 T5: the home
// screen resolves the active plot through them, and docs/07 §Estructura forbids
// one feature importing another. Re-exported here so this feature's own
// importers are unchanged.
export { useFarms, usePlotsByFarm }
export type { FarmView, PlotView }
export type { FarmsPage } from '../../../lib/api/farms'

export type IrrigationSystem = components['schemas']['IrrigationSystem']
export type GeoJsonPoint = components['schemas']['GeoJSONPoint']
export type SoilProfileView = components['schemas']['SoilProfileView']
export type SoilProfilePutRequest = components['schemas']['SoilProfilePutRequest']
export type CropView = components['schemas']['CropView']
export type CropCycleView = components['schemas']['CropCycleView']
export type CropCycleCreateRequest = components['schemas']['CropCycleCreateRequest']
export type CropCyclePatchRequest = components['schemas']['CropCyclePatchRequest']

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

function soilQueryKey(plotId: string) {
  return ['soil', plotId] as const
}

/**
 * docs/04-api.md:43-56 lists no `GET /plots/{plot_id}/soil` (T4 decision, same
 * gap noted there): this query never fetches, it only reads back whatever a
 * `PUT` or autofill response already cached this session via `setQueryData`
 * (T9). A fresh app session shows no soil data until one of those runs once.
 */
export function useSoilProfile(plotId: string) {
  return useQuery({
    queryKey: soilQueryKey(plotId),
    queryFn: (): Promise<SoilProfileView> =>
      Promise.reject(new Error('no GET /plots/{plot_id}/soil endpoint')),
    enabled: false,
    retry: false,
  })
}

export interface LatestReading {
  sensorId: number
  depthCm: number | null
  value: number
  at: string
}

/** Short window, not the 2 days `raw` allows: this block shows the latest value, and a
 * wider window would only cost points nobody reads. */
const READINGS_WINDOW_MS = 2 * 60 * 60 * 1000

export const SOIL_MOISTURE = 'soil_moisture'

/** docs/04-api.md:92-97 `GET /plots/{plot_id}/readings` reduced to the newest calibrated
 * point of each sensor series, which is all a "latest value" block needs. */
async function fetchLatestReadings(plotId: string): Promise<LatestReading[]> {
  const to = new Date()
  const from = new Date(to.getTime() - READINGS_WINDOW_MS)
  const { data, error } = await apiClient.GET('/api/v1/plots/{plot_id}/readings', {
    params: {
      path: { plot_id: plotId },
      query: {
        metric: SOIL_MOISTURE,
        from: from.toISOString(),
        to: to.toISOString(),
        resolution: 'raw',
      },
    },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from /plots/{plot_id}/readings')
  return data.series.flatMap((series) => {
    const last = series.points[series.points.length - 1]
    return last === undefined
      ? []
      : [{ sensorId: series.sensor_id, depthCm: series.depth_cm, value: last[1], at: last[0] }]
  })
}

export function useLatestReadings(plotId: string) {
  return useQuery({
    queryKey: ['latestReadings', plotId],
    queryFn: () => fetchLatestReadings(plotId),
  })
}

async function putSoil(plotId: string, input: SoilProfilePutRequest): Promise<SoilProfileView> {
  const { data, error } = await apiClient.PUT('/api/v1/plots/{plot_id}/soil', {
    params: { path: { plot_id: plotId } },
    body: input,
  })
  if (error) throw error
  if (!data) throw new Error('empty response from PUT /plots/{plot_id}/soil')
  return data
}

/** docs/04-api.md: `PUT /plots/{plot_id}/soil` (full-document replace, T4). Caches the
 * returned profile under this plot's `soilQueryKey` (T9: no GET endpoint to refetch from). */
export function usePutSoil(plotId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: SoilProfilePutRequest) => putSoil(plotId, input),
    onSuccess: (profile) => queryClient.setQueryData(soilQueryKey(plotId), profile),
  })
}

async function autofillSoil(plotId: string): Promise<SoilProfileView> {
  const { data, error } = await apiClient.POST('/api/v1/plots/{plot_id}/soil:autofill', {
    params: { path: { plot_id: plotId } },
  })
  if (error) throw error
  if (!data) throw new Error('empty response from POST /plots/{plot_id}/soil:autofill')
  return data
}

/** docs/04-api.md: `POST /plots/{plot_id}/soil:autofill` (RF-03, SoilGrids, T5). */
export function useAutofillSoil(plotId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => autofillSoil(plotId),
    onSuccess: (profile) => queryClient.setQueryData(soilQueryKey(plotId), profile),
  })
}

async function fetchCrops(): Promise<CropView[]> {
  const { data, error } = await apiClient.GET('/api/v1/crops', {})
  if (error) throw error
  if (!data) throw new Error('empty response from /crops')
  return data
}

/** docs/04-api.md: `GET /crops → Crop[]` (con etapas, Kc y kc_source, T3). Global reference
 * data (no org scoping), same query for every plot. */
export function useCrops() {
  return useQuery({ queryKey: ['crops'], queryFn: fetchCrops })
}

function activeCycleQueryKey(plotId: string) {
  return ['activeCycle', plotId] as const
}

/** Same gap as `useSoilProfile`: docs/04-api.md:43-56 has no `GET` to list or read a plot's
 * cycles, only `POST`/`PATCH` (T6 decision). Reads back whatever those cached this session. */
export function useActiveCycle(plotId: string) {
  return useQuery({
    queryKey: activeCycleQueryKey(plotId),
    queryFn: (): Promise<CropCycleView> =>
      Promise.reject(new Error('no GET endpoint for a plot’s crop cycles')),
    enabled: false,
    retry: false,
  })
}

async function createCycle(plotId: string, input: CropCycleCreateRequest): Promise<CropCycleView> {
  const { data, error } = await apiClient.POST('/api/v1/plots/{plot_id}/cycles', {
    params: { path: { plot_id: plotId } },
    body: input,
  })
  if (error) throw error
  if (!data) throw new Error('empty response from POST /plots/{plot_id}/cycles')
  return data
}

/** docs/04-api.md: `POST /plots/{plot_id}/cycles`. Caches the created cycle as this plot's
 * active cycle (T9: no GET endpoint to refetch from). */
export function useCreateCycle(plotId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CropCycleCreateRequest) => createCycle(plotId, input),
    onSuccess: (cycle) => queryClient.setQueryData(activeCycleQueryKey(plotId), cycle),
  })
}

async function patchCycle(cycleId: string, input: CropCyclePatchRequest): Promise<CropCycleView> {
  const { data, error } = await apiClient.PATCH('/api/v1/cycles/{cycle_id}', {
    params: { path: { cycle_id: cycleId } },
    body: input,
  })
  if (error) throw error
  if (!data) throw new Error('empty response from PATCH /cycles/{cycle_id}')
  return data
}

export interface PatchCycleInput {
  cycleId: string
  changes: CropCyclePatchRequest
}

/** docs/04-api.md: `PATCH /cycles/{cycle_id}`. Updates this plot's cached active cycle
 * (T9: ending a cycle, or overriding its expected harvest date). */
export function usePatchCycle(plotId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ cycleId, changes }: PatchCycleInput) => patchCycle(cycleId, changes),
    onSuccess: (cycle) => {
      if (cycle.status === 'harvested' || cycle.status === 'lost') {
        queryClient.setQueryData(activeCycleQueryKey(plotId), null)
      } else {
        queryClient.setQueryData(activeCycleQueryKey(plotId), cycle)
      }
    },
  })
}
