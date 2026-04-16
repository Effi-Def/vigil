import { useEffect, useState } from 'react'
import { GeoJSON, useMap } from 'react-leaflet'
import L from 'leaflet'

const RIVERS_GEOJSON_URL = 'https://naciscdn.org/naturalearth/10m/physical/ne_10m_rivers_lake_centerlines.geojson'

export default function HydroNetworkLayer({ enabled }) {
  const map = useMap()
  const [geoJsonData, setGeoJsonData] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!enabled) {
      setGeoJsonData(null)
      return
    }

    let cancelled = false

    async function fetchRivers() {
      setLoading(true)
      try {
        const response = await fetch(RIVERS_GEOJSON_URL)
        if (!response.ok) throw new Error('Failed to fetch rivers')
        const data = await response.json()
        if (!cancelled) {
          setGeoJsonData(data)
        }
      } catch (error) {
        console.warn('Could not load river network:', error)
        if (!cancelled) setGeoJsonData(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchRivers()

    return () => {
      cancelled = true
    }
  }, [enabled])

  if (!enabled || !geoJsonData) return null

  return (
    <GeoJSON
      data={geoJsonData}
      style={{
        color: '#58a6ff',
        weight: 1.2,
        opacity: 0.45,
        lineCap: 'round',
        lineJoin: 'round',
      }}
      onEachFeature={(feature, layer) => {
        layer.setStyle({
          interactive: false,
        })
      }}
    />
  )
}

/**
 * Optional: WMS layer for ARPA regional hydro data
 * Can be uncommented if ARPA WMS service is available
 */
export function HydroWMSLayer({ enabled }) {
  const map = useMap()

  useEffect(() => {
    if (!enabled) return undefined

    // ARPA Emilia-Romagna WMS endpoint (if available)
    // This would be configured based on actual ARPA service
    // const wmsLayer = L.tileLayer.wms('https://idro.cra.emilia-romagna.it/arcgis/services/...', {
    //   layers: 'hydro_network',
    //   format: 'image/png',
    //   transparent: true,
    //   opacity: 0.3,
    //   zIndex: 420,
    // })
    // wmsLayer.addTo(map)
    // return () => map.removeLayer(wmsLayer)

    // For now, we'll skip WMS until ARPA endpoint is confirmed
    return undefined
  }, [map, enabled])

  return null
}
