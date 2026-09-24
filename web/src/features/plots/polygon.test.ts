import { describe, expect, it } from 'vitest'
import { MIN_POLYGON_VERTICES, buildPolygonGeoJson } from './polygon'

describe('buildPolygonGeoJson', () => {
  it('closes the ring by repeating the first vertex as the last', () => {
    const polygon = buildPolygonGeoJson([
      { lat: 10, lng: -74 },
      { lat: 10.1, lng: -74 },
      { lat: 10.1, lng: -73.9 },
    ])

    const ring = polygon.coordinates[0]
    expect(ring[0]).toEqual(ring[ring.length - 1])
    expect(ring).toHaveLength(4)
  })

  it('emits positions in [lon, lat] order, not [lat, lon]', () => {
    const polygon = buildPolygonGeoJson([
      { lat: 10, lng: -74 },
      { lat: 10.1, lng: -74 },
      { lat: 10.1, lng: -73.9 },
    ])

    expect(polygon.type).toBe('Polygon')
    expect(polygon.coordinates[0][0]).toEqual([-74, 10])
  })

  it('rejects fewer than the minimum number of vertices', () => {
    expect(MIN_POLYGON_VERTICES).toBe(3)
    expect(() =>
      buildPolygonGeoJson([
        { lat: 10, lng: -74 },
        { lat: 10.1, lng: -74 },
      ]),
    ).toThrow(/at least 3/)
  })

  it('does not mutate the input vertices array', () => {
    const vertices = [
      { lat: 10, lng: -74 },
      { lat: 10.1, lng: -74 },
      { lat: 10.1, lng: -73.9 },
    ]
    buildPolygonGeoJson(vertices)
    expect(vertices).toHaveLength(3)
  })
})
