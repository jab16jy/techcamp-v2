import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Vertex } from '../polygon'

/**
 * Glue test with a mocked Leaflet (task instruction, T8's own progress notes
 * already justified skipping a real-Leaflet-in-jsdom test as brittle for
 * layout measurement — this proves the thin wiring instead: event handlers,
 * layer add/remove, cleanup).
 */
const mapInstance = {
  setView: vi.fn(),
  on: vi.fn(),
  remove: vi.fn(),
  removeLayer: vi.fn(),
  invalidateSize: vi.fn(),
}
mapInstance.setView.mockReturnValue(mapInstance)

const tileLayerInstance = { addTo: vi.fn() }
const layerInstance = { addTo: vi.fn() }

vi.mock('leaflet', () => ({
  default: {
    map: vi.fn(() => mapInstance),
    tileLayer: vi.fn(() => tileLayerInstance),
    polygon: vi.fn(() => layerInstance),
    circleMarker: vi.fn(() => layerInstance),
  },
}))

const L = (await import('leaflet')).default
const { default: PlotDrawMap } = await import('./PlotDrawMap')

function clickHandler(): (event: { latlng: Vertex }) => void {
  const call = mapInstance.on.mock.calls.find(([event]) => event === 'click')
  if (!call) throw new Error('no click handler registered')
  return call[1] as (event: { latlng: Vertex }) => void
}

describe('PlotDrawMap', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mapInstance.setView.mockReturnValue(mapInstance)
  })

  it('calls onMapClick with the clicked lat/lng', () => {
    const onMapClick = vi.fn()
    render(<PlotDrawMap vertices={[]} onMapClick={onMapClick} />)

    clickHandler()({ latlng: { lat: 10.5, lng: -74.5 } })

    expect(onMapClick).toHaveBeenCalledWith({ lat: 10.5, lng: -74.5 })
  })

  it('removes the map on unmount', () => {
    const { unmount } = render(<PlotDrawMap vertices={[]} onMapClick={vi.fn()} />)

    unmount()

    expect(mapInstance.remove).toHaveBeenCalledTimes(1)
  })

  it('invalidates the map size after mount, once the container has settled', async () => {
    render(<PlotDrawMap vertices={[]} onMapClick={vi.fn()} />)

    await waitFor(() => expect(mapInstance.invalidateSize).toHaveBeenCalledTimes(1))
  })

  it('draws a circleMarker (not the default Leaflet marker icon) for a single vertex', () => {
    const { rerender } = render(<PlotDrawMap vertices={[]} onMapClick={vi.fn()} />)

    rerender(<PlotDrawMap vertices={[{ lat: 10, lng: -74 }]} onMapClick={vi.fn()} />)

    expect(L.circleMarker).toHaveBeenCalledWith([10, -74])
  })

  it('adds the OpenStreetMap tile layer with the required attribution (Leaflet attribution control)', () => {
    render(<PlotDrawMap vertices={[]} onMapClick={vi.fn()} />)

    const [, options] = vi.mocked(L.tileLayer).mock.calls[0]
    expect(options?.attribution).toContain('OpenStreetMap')
  })
})
