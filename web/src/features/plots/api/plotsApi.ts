import { apiFetch } from '../../../lib/api/client'

export interface FarmView {
  id: string
  org_id: string
  name: string
}

export type IrrigationSystem = 'none' | 'drip' | 'sprinkler' | 'gravity'

export interface PlotView {
  id: string
  farm_id: string
  name: string
  area_ha: number
  irrigation_system: IrrigationSystem
}

interface FarmPage {
  items: FarmView[]
  next_cursor: string | null
}

/** docs/04-api.md: `GET /farms?org_id= → Page<Farm>`. */
async function fetchFarms(orgId: string): Promise<FarmView[]> {
  const page = await apiFetch<FarmPage>(`/farms?org_id=${encodeURIComponent(orgId)}`)
  return page.items
}

/** docs/04-api.md: `GET /farms/{farm_id}/plots → Plot[]`. */
function fetchPlots(farmId: string): Promise<PlotView[]> {
  return apiFetch<PlotView[]>(`/farms/${encodeURIComponent(farmId)}/plots`)
}

export interface FarmWithPlots {
  farm: FarmView
  plots: PlotView[]
}

/** One combined load for the plots tab: the org's farms, then each farm's plots. */
export async function loadFarmsWithPlots(orgId: string): Promise<FarmWithPlots[]> {
  const farms = await fetchFarms(orgId)
  const plotsByFarm = await Promise.all(farms.map((farm) => fetchPlots(farm.id)))
  return farms.map((farm, index) => ({ farm, plots: plotsByFarm[index] }))
}
