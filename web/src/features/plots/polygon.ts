/** A map-clicked vertex, in Leaflet's own `{lat, lng}` shape. */
export interface Vertex {
  lat: number
  lng: number
}

/** A closed ring needs at least 4 positions (server: `GeoJSONPolygon._rings_are_closed`,
 * `server/src/techcamp/farms/adapters/api/router.py`); closing a ring adds one, so this is
 * the minimum number of distinct vertices a user must place. */
export const MIN_POLYGON_VERTICES = 3

export interface GeoJsonPolygon {
  type: 'Polygon'
  coordinates: [number, number][][]
}

/** Builds a closed GeoJSON `Polygon` from drawn vertices: `[lon, lat]` position order
 * (RFC 7946 / `server/src/techcamp/farms/adapters/geojson.py`'s `x, y` = lon, lat), ring
 * closed by repeating the first vertex as the last. */
export function buildPolygonGeoJson(vertices: Vertex[]): GeoJsonPolygon {
  if (vertices.length < MIN_POLYGON_VERTICES) {
    throw new Error(`a plot boundary needs at least ${MIN_POLYGON_VERTICES} vertices`)
  }
  const ring: [number, number][] = vertices.map((v) => [v.lng, v.lat])
  ring.push(ring[0])
  return { type: 'Polygon', coordinates: [ring] }
}
