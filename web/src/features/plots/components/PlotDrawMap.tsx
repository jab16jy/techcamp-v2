import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef } from 'react'
import type { Vertex } from '../polygon'

/**
 * Caribbean Colombia (roughly Cesar/La Guajira/Magdalena), the region this
 * product targets (`AGENTS.md`) — a reasonable starting view, not a
 * requirement; the user pans/zooms to their own plot.
 */
const DEFAULT_CENTER: L.LatLngTuple = [10.4, -73.25]
const DEFAULT_ZOOM = 8

/**
 * Tile source: docs/07-frontend-design-system.md and ADR-0021 (seminar
 * profile) don't name one — a doc gap, flagged in the ODD progress notes.
 * OpenStreetMap's public tile server is the smallest reasonable default: no
 * API key, no account, and Leaflet's own docs use it as the baseline example.
 */
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'

export interface PlotDrawMapProps {
  vertices: Vertex[]
  onMapClick: (vertex: Vertex) => void
}

/**
 * Click-to-add-vertex polygon drawing (ponytail: plain Leaflet, no draw
 * plugin — a click handler plus `L.polygon`/`L.marker` is a smaller, fully
 * understood diff than a plugin dependency for this one interaction).
 * Lazy-loaded by its caller (`React.lazy`) so Leaflet stays out of the main
 * bundle (docs/05-arquitectura.md:179).
 */
export default function PlotDrawMap({ vertices, onMapClick }: PlotDrawMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.Layer | null>(null)
  const onMapClickRef = useRef(onMapClick)

  useEffect(() => {
    onMapClickRef.current = onMapClick
  }, [onMapClick])

  useEffect(() => {
    if (!containerRef.current) return
    const map = L.map(containerRef.current).setView(DEFAULT_CENTER, DEFAULT_ZOOM)
    L.tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: 19 }).addTo(map)
    map.on('click', (event: L.LeafletMouseEvent) => {
      onMapClickRef.current({ lat: event.latlng.lat, lng: event.latlng.lng })
    })
    mapRef.current = map
    // The sheet slides in via a CSS transform (`sheet.css`'s `translateY`),
    // which doesn't change the container's box size, but Leaflet's very
    // first tile fetch can still measure a not-yet-settled layout on the
    // first paint inside an animating ancestor. One deferred
    // `invalidateSize()` re-measures it once the browser has settled
    // (#21 round 12 suggestion).
    const resizeTimer = setTimeout(() => map.invalidateSize(), 0)
    return () => {
      clearTimeout(resizeTimer)
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (layerRef.current) {
      map.removeLayer(layerRef.current)
      layerRef.current = null
    }
    if (vertices.length === 0) return
    const latLngs: L.LatLngTuple[] = vertices.map((v) => [v.lat, v.lng])
    // `L.circleMarker` for the first vertex, not `L.marker`: the default
    // marker icon's relative asset paths (`marker-icon.png`, `marker-
    // shadow.png`) break under Vite's bundling (find-docs/ctx7:
    // github.com/leaflet/leaflet DefaultIcon.js) — this needs no icon
    // import at all (ponytail: smaller than fixing the asset paths).
    const layer = vertices.length >= 2 ? L.polygon(latLngs) : L.circleMarker(latLngs[0])
    layer.addTo(map)
    layerRef.current = layer
  }, [vertices])

  return (
    <div
      ref={containerRef}
      role="application"
      aria-label="Mapa para dibujar el polígono de la parcela"
      className="h-64 w-full rounded-md"
    />
  )
}
