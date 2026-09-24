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
    return () => {
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
    const layer = vertices.length >= 2 ? L.polygon(latLngs) : L.marker(latLngs[0])
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
