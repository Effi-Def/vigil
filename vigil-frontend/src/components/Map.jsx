import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { Circle, CircleMarker, MapContainer, Marker, Polyline, Popup, TileLayer, Tooltip, useMap } from 'react-leaflet'
import DataQualityBadge from './DataQualityBadge'
import { CATEGORY_META, FALLBACK_CATEGORY } from '../constants/categoryMeta'
import { SEVERITY_CONFIG, getSeverity } from '../constants/severity'
import { isEventInTimeRange } from '../hooks/useTimelineFilter'
import HydroNetworkLayer from './HydroNetworkLayer'
import styles from './Map.module.css'

const API_BASE = import.meta.env.DEV
  ? (import.meta.env.VITE_API_TARGET || 'http://127.0.0.1:8001')
  : '/api'

const USE_DEFAULT_MARKER_DIAGNOSTIC = false

const REGION_HINTS = [
  { re: /\b(italy|italia|lombardia|veneto|toscana|lazio)\b/, lat: 42.5, lon: 12.5 },
  { re: /(europe|france|germany|spain)/, lat: 50, lon: 12 },
  { re: /(north america|canada|united states|usa)/, lat: 43, lon: -100 },
  { re: /(south america|brazil|argentina|peru|chile)/, lat: -17, lon: -62 },
  { re: /(africa|ethiopia|kenya|nigeria)/, lat: 5, lon: 20 },
  { re: /(asia|china|india|japan|myanmar)/, lat: 32, lon: 90 },
  { re: /(oceania|australia|new zealand)/, lat: -23, lon: 138 },
]

const SYNOPTIC_FALLBACK = [
  {
    id: 'surface-analysis-global',
    title: 'Carta sinottica globale · isobare',
    source: 'NOAA Ocean Prediction Center',
    url: 'https://ocean.weather.gov/P_sfc_full_ocean_color.png',
  },
  {
    id: 'surface-analysis-atlantic',
    title: 'Pressione al suolo Nord Atlantico / Europa',
    source: 'Deutscher Wetterdienst',
    url: 'https://www.dwd.de/DWD/wetter/wv_spez/hobbymet/wetterkarten/bwk_bodendruck_na_ana.png',
  },
]

function hashText(text) {
  let h = 2166136261
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function estimateLatLon(ev) {
  if (ev.lat != null && ev.lon != null) return { lat: ev.lat, lon: ev.lon }
  const raw = `${ev.region || ''} ${ev.title || ''}`.toLowerCase()
  for (const h of REGION_HINTS) {
    if (h.re.test(raw)) return { lat: h.lat, lon: h.lon }
  }
  const hash = hashText(raw || String(ev.id || 'vigil'))
  const lon = -25 + ((hash % 7000) / 100)
  const lat = 34 + (((Math.floor(hash / 7000) % 3800) / 100))
  return { lat, lon }
}

function categoryForEvent(ev) {
  if (ev.category && CATEGORY_META[ev.category]) return ev.category
  if (ev.type && CATEGORY_META[ev.type]) return ev.type
  return 'other'
}

function prettyEventTitle(ev) {
  const raw = String(ev?.title || '').trim()
  if (!raw) return 'Evento senza titolo'
  let title = raw
    .replace(/^Orange\s+/i, 'Allerta arancione ')
    .replace(/^Yellow\s+/i, 'Allerta gialla ')
    .replace(/^Red\s+/i, 'Allerta rossa ')
    .replace(/Warning issued for Italy\s*-\s*/i, '')
    .replace(/issued for Italy\s*-\s*/i, '')
    .replace(/Thunderstorm/gi, 'temporali')
    .replace(/Snow-ice/gi, 'neve/ghiaccio')
    .replace(/Wind/gi, 'vento')
  return title.replace(/\s+/g, ' ').trim()
}

function prettyPlatformLabel(value) {
  const raw = String(value || '').trim().toLowerCase()
  const labels = {
    meteoalarm: 'Meteoalarm',
    dpc_vigilanza: 'Protezione Civile',
    rss: 'Rassegna stampa',
    peertube: 'Video pubblici',
    usgs: 'USGS',
  }
  return labels[raw] || String(value || '')
}

function categoryAbbr(meta, ev) {
  const base = String(meta?.label || ev?.category || ev?.type || 'NA').trim()
  const parts = base.split(/[\s_-]+/).filter(Boolean)
  if (parts.length >= 2) return `${parts[0][0] || ''}${parts[1][0] || ''}`.toUpperCase()
  return base.slice(0, 2).toUpperCase()
}

function keyStatForEvent(ev, meta) {
  const priority = Number(meta?.priority || 5)
  const picksByPriority = {
    1: [
      ['Magnitudo', ev.magnitude != null ? `${ev.magnitude} Mw` : null],
      ['Vento', ev.wind_kmh != null ? `${ev.wind_kmh} km/h` : null],
      ['Profondità', ev.depth_km != null ? `${ev.depth_km} km` : null],
    ],
    2: [
      ['Vento', ev.wind_kmh != null ? `${ev.wind_kmh} km/h` : null],
      ['Temperatura', ev.temp_c != null ? `${ev.temp_c} C` : null],
      ['Precipitazioni', ev.precipitation_mm != null ? `${ev.precipitation_mm} mm` : null],
    ],
    3: [
      ['Stato', ev.status || null],
      ['Severità', (SEVERITY_CONFIG[getSeverity(ev?.severity)] || SEVERITY_CONFIG.unknown).label],
      ['Media', ev.media_count != null ? `${ev.media_count}` : null],
    ],
  }
  const ordered = picksByPriority[priority] || picksByPriority[3]
  const found = ordered.find(([, value]) => value)
  if (found) return `${found[0]}: ${found[1]}`
  return `Stato: ${ev.status || '-'}`
}

function spreadNearbyMarkers(rows, zoom = 8) {
  const bucketSize = zoom >= 9 ? 0.03 : zoom >= 7 ? 0.06 : 0.12
  const groups = new Map()

  rows.forEach((row) => {
    const key = `${Math.round(row.pos.lat / bucketSize)}:${Math.round(row.pos.lon / bucketSize)}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(row)
  })

  const offset = zoom >= 9 ? 0.05 : zoom >= 7 ? 0.11 : 0.18
  return rows.map((row) => {
    const key = `${Math.round(row.pos.lat / bucketSize)}:${Math.round(row.pos.lon / bucketSize)}`
    const group = groups.get(key) || []
    if (group.length <= 1) return row
    const idx = group.findIndex((item) => item.ev.id === row.ev.id)
    const angle = (idx / group.length) * Math.PI * 2
    return {
      ...row,
      pos: {
        lat: row.pos.lat + Math.sin(angle) * offset,
        lon: row.pos.lon + Math.cos(angle) * offset,
      },
    }
  })
}

function buildDisplayMarkers(rows, map, zoom) {
  if (!map) return rows.map((row) => ({ kind: 'event', ...row }))

  if (zoom >= 8) {
    return spreadNearbyMarkers(rows, zoom).map((row) => ({ kind: 'event', ...row }))
  }

  const cellSize = zoom <= 4 ? 96 : zoom <= 6 ? 76 : 62
  const clusters = new Map()

  rows.forEach((row) => {
    const point = map.project([row.pos.lat, row.pos.lon], zoom)
    const key = `${Math.round(point.x / cellSize)}:${Math.round(point.y / cellSize)}`
    if (!clusters.has(key)) clusters.set(key, [])
    clusters.get(key).push(row)
  })

  return Array.from(clusters.entries()).map(([key, items]) => {
    if (items.length === 1) return { kind: 'event', ...items[0] }
    const lat = items.reduce((sum, item) => sum + item.pos.lat, 0) / items.length
    const lon = items.reduce((sum, item) => sum + item.pos.lon, 0) / items.length
    return {
      kind: 'cluster',
      id: `cluster-${key}`,
      lat,
      lon,
      count: items.length,
      items,
    }
  })
}

function clusterIcon(count, selected = false) {
  const size = count >= 12 ? 34 : count >= 6 ? 30 : 26
  const tone = count >= 12 ? '#f85149' : count >= 6 ? '#d29922' : '#58a6ff'
  const shadow = selected
    ? '0 0 0 2px rgba(255,255,255,0.96), 0 8px 18px rgba(15,23,42,0.28)'
    : '0 4px 12px rgba(15,23,42,0.18)'
  return L.divIcon({
    className: 'vigil-cluster-marker',
    html: `<div class="${styles.clusterMarker}" style="width:${size}px;height:${size}px;background:${tone};box-shadow:${shadow};">${count}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

function offsetLatLon(lat, lon, angleRad, magnitudeDeg) {
  const latDelta = Math.sin(angleRad) * magnitudeDeg
  const lonDelta = (Math.cos(angleRad) * magnitudeDeg) / Math.max(Math.cos((lat * Math.PI) / 180), 0.35)
  return { lat: lat + latDelta, lon: lon + lonDelta }
}

function pseudoWindAngle(lat, lon, speed = 0) {
  const seed = hashText(`${lat.toFixed(3)}:${lon.toFixed(3)}:${Math.round(speed)}`)
  return ((seed % 360) * Math.PI) / 180
}

function windAngleFromPoint(point, lat, lon, speed) {
  const explicitDir = Number(point?.direction_deg ?? point?.weather?.wind_direction_deg)
  if (Number.isFinite(explicitDir)) {
    const towardDeg = (explicitDir + 180) % 360
    return ((90 - towardDeg) * Math.PI) / 180
  }
  return pseudoWindAngle(lat, lon, speed)
}

function windColorForSpeed(speed = 0) {
  if (speed >= 70) return '#f85149'
  if (speed >= 35) return '#d29922'
  return '#58a6ff'
}

function buildWindSegments(point, zoom = 7) {
  const lat = Number(point?.lat)
  const lon = Number(point?.lon)
  const speed = Number((point?.wind_kmh ?? point?.weather?.wind_kmh) || 0)
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || speed <= 0) return []

  const baseAngle = windAngleFromPoint(point, lat, lon, speed)
  const zoomBoost = Math.max(0, 8 - Math.min(zoom, 8))
  const length = Math.max(0.12, Math.min(1.05, 0.12 + (speed / 135) + (zoomBoost * 0.12)))
  const crossSpread = Math.max(0.09, Math.min(0.26, 0.09 + (zoomBoost * 0.03)))
  const offsets = [-crossSpread, -(crossSpread * 0.66), -(crossSpread * 0.33), 0, crossSpread * 0.33, crossSpread * 0.66, crossSpread]

  return offsets.map((cross, idx) => {
    const center = offsetLatLon(lat, lon, baseAngle + Math.PI / 2, cross)
    const start = offsetLatLon(center.lat, center.lon, baseAngle + Math.PI, length * (0.42 + idx * 0.05))
    const end = offsetLatLon(center.lat, center.lon, baseAngle, length * (0.58 + idx * 0.03))
    return {
      positions: [[start.lat, start.lon], [end.lat, end.lon]],
      opacity: Math.min(0.92, 0.4 + (speed / 110)),
      weight: Math.max(2.1, Math.min(4.6, 2 + (speed / 28))),
    }
  })
}

function buildWindArrowSegments(point, zoom = 7) {
  const lat = Number(point?.lat)
  const lon = Number(point?.lon)
  const speed = Number((point?.wind_kmh ?? point?.weather?.wind_kmh) || 0)
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || speed <= 0) return []

  const baseAngle = windAngleFromPoint(point, lat, lon, speed)
  const zoomBoost = Math.max(0, 8 - Math.min(zoom, 8))
  const shaft = Math.max(0.16, Math.min(1.1, 0.15 + (speed / 120) + (zoomBoost * 0.12)))
  const head = Math.max(0.05, Math.min(0.16, shaft * 0.24))

  const tail = offsetLatLon(lat, lon, baseAngle + Math.PI, shaft * 0.34)
  const tip = offsetLatLon(lat, lon, baseAngle, shaft * 0.66)
  const left = offsetLatLon(tip.lat, tip.lon, baseAngle + Math.PI - 0.52, head)
  const right = offsetLatLon(tip.lat, tip.lon, baseAngle + Math.PI + 0.52, head)

  return [
    {
      positions: [[tail.lat, tail.lon], [tip.lat, tip.lon]],
      opacity: Math.min(0.92, 0.42 + (speed / 110)),
      weight: Math.max(2.4, Math.min(4.6, 2.2 + (speed / 28))),
    },
    {
      positions: [[left.lat, left.lon], [tip.lat, tip.lon], [right.lat, right.lon]],
      opacity: Math.min(0.95, 0.46 + (speed / 100)),
      weight: Math.max(1.8, Math.min(3.8, 1.8 + (speed / 36))),
    },
  ]
}

function buildHydroColoredSegments(path, riverLine) {
  if (!Array.isArray(path) || path.length < 2) return []

  const stationRefs = Array.isArray(riverLine?.stations)
    ? riverLine.stations
      .map((station) => ({
        lat: Number(station?.lat),
        lon: Number(station?.lon),
        level: String(station?.level || riverLine?.level || 'normal').toLowerCase(),
        color: station?.color || riverLine?.color || '#3fb950',
      }))
      .filter((station) => Number.isFinite(station.lat) && Number.isFinite(station.lon))
    : []

  const mergedSegments = []
  for (let idx = 1; idx < path.length; idx += 1) {
    const start = path[idx - 1]
    const end = path[idx]
    const midLat = (Number(start[0]) + Number(end[0])) / 2
    const midLon = (Number(start[1]) + Number(end[1])) / 2

    let chosenLevel = String(riverLine?.level || 'normal').toLowerCase()
    let chosenColor = riverLine?.color || '#3fb950'

    if (stationRefs.length) {
      const nearest = stationRefs.reduce((best, station) => {
        const dist = ((station.lat - midLat) ** 2) + ((station.lon - midLon) ** 2)
        return !best || dist < best.dist ? { ...station, dist } : best
      }, null)
      if (nearest) {
        chosenLevel = nearest.level
        chosenColor = nearest.color
      }
    }

    const prev = mergedSegments[mergedSegments.length - 1]
    if (prev && prev.color === chosenColor && prev.level === chosenLevel) {
      prev.positions.push([Number(end[0]), Number(end[1])])
    } else {
      mergedSegments.push({
        positions: [[Number(start[0]), Number(start[1])], [Number(end[0]), Number(end[1])]],
        color: chosenColor,
        level: chosenLevel,
      })
    }
  }

  return mergedSegments
}

function smoothPath(path, iterations = 2) {
  if (!Array.isArray(path) || path.length < 2) return []

  let current = path
    .map((point) => [Number(point?.[0]), Number(point?.[1])])
    .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]))

  for (let step = 0; step < iterations; step += 1) {
    if (current.length < 3) break
    const next = [current[0]]
    for (let idx = 0; idx < current.length - 1; idx += 1) {
      const start = current[idx]
      const end = current[idx + 1]
      next.push([
        (start[0] * 0.75) + (end[0] * 0.25),
        (start[1] * 0.75) + (end[1] * 0.25),
      ])
      next.push([
        (start[0] * 0.25) + (end[0] * 0.75),
        (start[1] * 0.25) + (end[1] * 0.75),
      ])
    }
    next.push(current[current.length - 1])
    current = next
  }

  return current
}

function buildRiverRenderPath(path, riverKey = '') {
  const base = Array.isArray(path)
    ? path
      .map((point) => [Number(point?.[0]), Number(point?.[1])])
      .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]))
    : []

  if (base.length < 2) return []

  if (base.length === 2) {
    const [start, end] = base
    const latDelta = end[0] - start[0]
    const lonDelta = end[1] - start[1]
    const distance = Math.hypot(latDelta, lonDelta) || 1
    const bend = Math.min(0.18, Math.max(0.045, distance * 0.18))
    const sign = hashText(`${riverKey}:${start[0].toFixed(3)}:${start[1].toFixed(3)}:${end[0].toFixed(3)}:${end[1].toFixed(3)}`) % 2 === 0 ? 1 : -1
    const mid = [
      ((start[0] + end[0]) / 2) + ((-lonDelta / distance) * bend * sign),
      ((start[1] + end[1]) / 2) + ((latDelta / distance) * bend * sign),
    ]
    return smoothPath([start, mid, end], 3)
  }

  return smoothPath(base, base.length <= 4 ? 2 : 1)
}

function markerIcon(ev, selectedId) {
  if (USE_DEFAULT_MARKER_DIAGNOSTIC) return new L.Icon.Default()
  const category = categoryForEvent(ev)
  const meta = CATEGORY_META[category] || FALLBACK_CATEGORY
  const sev = getSeverity(ev?.severity)
  const sevCfg = SEVERITY_CONFIG[sev] || SEVERITY_CONFIG.unknown
  const selected = selectedId === ev.id
  const border = sevCfg.color
  const shadow = selected
    ? '0 0 0 1px #0d1117 inset, 0 0 0 2px rgba(255,255,255,0.92), 0 6px 14px rgba(15,23,42,0.22)'
    : '0 2px 8px rgba(15,23,42,0.14)'
  const isRed = sev === 'critical'
  const haloHtml = isRed
    ? `<svg class="vigil-halo" viewBox="0 0 44 44" xmlns="http://www.w3.org/2000/svg"><circle cx="22" cy="22" r="16" fill="none" stroke="${sevCfg.color}" stroke-width="2"/></svg>`
    : ''
  return L.divIcon({
    className: 'vigil-marker',
    html: `<div style="position:relative;width:24px;height:24px;">${haloHtml}<div class="${styles.eventMarker}" style="border-color:${border};box-shadow:${shadow};"><span>${meta.icon}</span></div></div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  })
}

function RadarLayer({ enabled, url }) {
  const map = useMap()
  useEffect(() => {
    if (!enabled || !url) return undefined
    const layer = L.tileLayer(url, { opacity: 0.4, zIndex: 450, maxZoom: 12, attribution: 'RainViewer' })
    layer.addTo(map)
    return () => map.removeLayer(layer)
  }, [enabled, url, map])
  return null
}

function SatIrLayer({ enabled }) {
  const map = useMap()
  useEffect(() => {
    if (!enabled) return undefined
    const layer = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      { opacity: 0.55, zIndex: 430, maxZoom: 18, attribution: 'Esri World Imagery' },
    )
    layer.addTo(map)
    return () => map.removeLayer(layer)
  }, [enabled, map])
  return null
}

function MapAutoFix() {
  const map = useMap()

  useEffect(() => {
    const invalidate = () => {
      try {
        map.invalidateSize(false)
      } catch {
        // ignore transient leaflet sizing issues during mount/unmount
      }
    }

    const raf = requestAnimationFrame(invalidate)
    const t1 = setTimeout(invalidate, 120)
    const t2 = setTimeout(invalidate, 700)
    window.addEventListener('resize', invalidate)

    let observer = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => invalidate())
      observer.observe(map.getContainer())
    }

    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(t1)
      clearTimeout(t2)
      window.removeEventListener('resize', invalidate)
      observer?.disconnect()
    }
  }, [map])

  return null
}

function WindFieldLayer({ enabled }) {
  const map = useMap()
  const [points, setPoints] = useState([])
  const canvasRef = useRef(null)
  const animationRef = useRef(null)
  const particlesRef = useRef([])

  useEffect(() => {
    if (!enabled) {
      setPoints([])
      return
    }

    let cancelled = false
    let timer = null

    async function fetchWindField() {
      const b = map.getBounds()
      const z = Math.round(map.getZoom())
      const url = `${API_BASE}/geo/wind-field?min_lat=${b.getSouth()}&max_lat=${b.getNorth()}&min_lon=${b.getWest()}&max_lon=${b.getEast()}&zoom=${z}`
      try {
        const res = await fetch(url)
        if (!res.ok) return
        const data = await res.json()
        if (!cancelled) setPoints(Array.isArray(data?.points) ? data.points : [])
      } catch {
        if (!cancelled) setPoints([])
      }
    }

    fetchWindField()
    const onMoveEnd = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(fetchWindField, 220)
    }
    map.on('moveend', onMoveEnd)
    map.on('zoomend', onMoveEnd)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      map.off('moveend', onMoveEnd)
      map.off('zoomend', onMoveEnd)
    }
  }, [map, enabled])

  useEffect(() => {
    if (!enabled || !points.length) {
      if (animationRef.current) cancelAnimationFrame(animationRef.current)
      animationRef.current = null
      if (canvasRef.current?.parentNode) {
        canvasRef.current.parentNode.removeChild(canvasRef.current)
      }
      canvasRef.current = null
      particlesRef.current = []
      return undefined
    }

    const pane = map.getPanes().overlayPane
    const canvas = document.createElement('canvas')
    canvas.className = `leaflet-zoom-animated ${styles.windCanvas}`
    pane.appendChild(canvas)
    canvasRef.current = canvas

    const ctx = canvas.getContext('2d')
    if (!ctx) {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas)
      return undefined
    }

    const positionCanvas = () => {
      const size = map.getSize()
      const topLeft = map.containerPointToLayerPoint([0, 0])
      L.DomUtil.setPosition(canvas, topLeft)
      canvas.style.width = `${size.x}px`
      canvas.style.height = `${size.y}px`
    }

    const maxAge = 90

    const spawnParticle = (randomizeAge = true) => {
      const size = map.getSize()
      return {
        x: Math.random() * size.x,
        y: Math.random() * size.y,
        age: randomizeAge ? Math.floor(Math.random() * maxAge) : 0,
      }
    }

    const sampleVector = (lat, lon) => {
      const origin = map.latLngToContainerPoint([lat, lon])
      let dxSum = 0
      let dySum = 0
      let speedSum = 0
      let totalWeight = 0

      for (const point of points) {
        const pointLat = Number(point?.lat)
        const pointLon = Number(point?.lon)
        const speed = Number((point?.wind_kmh ?? point?.weather?.wind_kmh) || 0)
        if (!Number.isFinite(pointLat) || !Number.isFinite(pointLon) || speed <= 0) continue

        const dist2 = ((pointLat - lat) ** 2) + ((pointLon - lon) ** 2)
        if (dist2 > 18) continue

        const weight = 1 / Math.max(dist2, 0.03)
        const angle = windAngleFromPoint(point, pointLat, pointLon, speed)
        const target = offsetLatLon(lat, lon, angle, Math.max(0.018, Math.min(0.085, 0.01 + (speed / 600))))
        const destination = map.latLngToContainerPoint([target.lat, target.lon])
        dxSum += (destination.x - origin.x) * weight
        dySum += (destination.y - origin.y) * weight
        speedSum += speed * weight
        totalWeight += weight
      }

      if (!totalWeight) return null
      return {
        dx: dxSum / totalWeight,
        dy: dySum / totalWeight,
        speed: speedSum / totalWeight,
      }
    }

    const resetParticles = () => {
      const size = map.getSize()
      const ratio = Math.max(1, window.devicePixelRatio || 1)
      positionCanvas()
      canvas.width = Math.round(size.x * ratio)
      canvas.height = Math.round(size.y * ratio)
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      ctx.clearRect(0, 0, size.x, size.y)

      const particleCount = Math.max(320, Math.min(900, Math.round((size.x * size.y) / 1900)))
      particlesRef.current = Array.from({ length: particleCount }, () => spawnParticle(true))
    }

    const draw = () => {
      const size = map.getSize()
      ctx.globalCompositeOperation = 'destination-in'
      ctx.fillStyle = 'rgba(255,255,255,0.972)'
      ctx.fillRect(0, 0, size.x, size.y)
      ctx.globalCompositeOperation = 'source-over'

      particlesRef.current.forEach((particle) => {
        if (particle.age > maxAge) {
          Object.assign(particle, spawnParticle(false))
          return
        }

        const latLng = map.containerPointToLatLng([particle.x, particle.y])
        const vector = sampleVector(latLng.lat, latLng.lng)
        if (!vector || !Number.isFinite(vector.dx) || !Number.isFinite(vector.dy) || vector.speed <= 0.2) {
          Object.assign(particle, spawnParticle(false))
          return
        }

        const drift = 0.55 + Math.min(1.75, vector.speed / 45)
        const nextX = particle.x + (vector.dx * drift)
        const nextY = particle.y + (vector.dy * drift)

        if (nextX < -20 || nextY < -20 || nextX > size.x + 20 || nextY > size.y + 20) {
          Object.assign(particle, spawnParticle(false))
          return
        }

        const strokeColor = windColorForSpeed(vector.speed)
        ctx.beginPath()
        ctx.moveTo(particle.x, particle.y)
        ctx.lineTo(nextX, nextY)
        ctx.strokeStyle = strokeColor
        ctx.shadowColor = strokeColor
        ctx.shadowBlur = 7
        ctx.globalAlpha = Math.min(0.86, 0.32 + (vector.speed / 110))
        ctx.lineWidth = Math.max(1.2, Math.min(3.1, 1.15 + (vector.speed / 68)))
        ctx.stroke()

        particle.x = nextX
        particle.y = nextY
        particle.age += 1
      })

      ctx.globalAlpha = 1
      ctx.shadowBlur = 0
      animationRef.current = requestAnimationFrame(draw)
    }

    resetParticles()
    animationRef.current = requestAnimationFrame(draw)

    const handleRefresh = () => resetParticles()
    map.on('moveend', handleRefresh)
    map.on('zoomend', handleRefresh)
    map.on('resize', handleRefresh)

    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current)
      animationRef.current = null
      map.off('moveend', handleRefresh)
      map.off('zoomend', handleRefresh)
      map.off('resize', handleRefresh)
      particlesRef.current = []
      if (canvas.parentNode) {
        canvas.parentNode.removeChild(canvas)
      }
      if (canvasRef.current === canvas) canvasRef.current = null
    }
  }, [enabled, map, points])

  if (!enabled || !points.length) return null

  const anchorPoints = points

  return (
    <>
      {anchorPoints.map((point, idx) => {
        const speed = Number((point?.wind_kmh ?? point?.weather?.wind_kmh) || 0)
        const color = windColorForSpeed(speed)
        const zoom = map.getZoom()
        const showArrowGuides = false

        return (
          <Fragment key={`wind-field-${idx}-${point.lat}-${point.lon}`}>
            {showArrowGuides && buildWindArrowSegments(point, zoom).map((segment, sidx) => (
              <Polyline
                key={`wind-arrow-${idx}-${sidx}`}
                positions={segment.positions}
                interactive={false}
                pathOptions={{
                  color,
                  opacity: segment.opacity,
                  weight: segment.weight,
                  className: 'windGuideLine',
                }}
              />
            ))}
            {buildWindSegments(point, zoom).map((segment, sidx) => (
              <Polyline
                key={`wind-field-${idx}-${sidx}`}
                positions={segment.positions}
                interactive={false}
                pathOptions={{
                  color,
                  opacity: Math.min(0.94, segment.opacity),
                  weight: Math.max(2.4, segment.weight + 0.45),
                  dashArray: '10 16',
                  className: 'windStreamLine',
                }}
              />
            ))}
          </Fragment>
        )
      })}
    </>
  )
}

function StationsLayer({ activeLayers }) {
  const map = useMap()
  const [stations, setStations] = useState([])
  const [riverGeoms, setRiverGeoms] = useState([])

  useEffect(() => {
    const needsStations = activeLayers.hydro || activeLayers.pluvio || activeLayers.precip
    if (!needsStations) {
      setStations([])
      return
    }

    let cancelled = false
    let timer = null

    async function fetchStations() {
      const b = map.getBounds()
      const url = `${API_BASE}/geo/stations?min_lat=${b.getSouth()}&max_lat=${b.getNorth()}&min_lon=${b.getWest()}&max_lon=${b.getEast()}&limit=120`
      try {
        const res = await fetch(url)
        if (!res.ok) return
        const data = await res.json()
        if (!cancelled) setStations(Array.isArray(data?.stations) ? data.stations : [])
      } catch {
        if (!cancelled) setStations([])
      }
    }

    fetchStations()
    const onMoveEnd = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(fetchStations, 220)
    }
    map.on('moveend', onMoveEnd)
    map.on('zoomend', onMoveEnd)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      map.off('moveend', onMoveEnd)
      map.off('zoomend', onMoveEnd)
    }
  }, [map, activeLayers.hydro, activeLayers.pluvio, activeLayers.precip])

  useEffect(() => {
    if (!activeLayers.hydro) {
      setRiverGeoms([])
      return
    }

    let cancelled = false
    let timer = null

    async function fetchRivers() {
      const b = map.getBounds()
      const url = `${API_BASE}/geo/hydro-rivers?min_lat=${b.getSouth()}&max_lat=${b.getNorth()}&min_lon=${b.getWest()}&max_lon=${b.getEast()}&limit=20`
      try {
        const res = await fetch(url)
        if (!res.ok) return
        const data = await res.json()
        if (!cancelled) setRiverGeoms(Array.isArray(data?.rivers) ? data.rivers : [])
      } catch {
        if (!cancelled) setRiverGeoms([])
      }
    }

    fetchRivers()
    const onMoveEnd = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(fetchRivers, 260)
    }
    map.on('moveend', onMoveEnd)
    map.on('zoomend', onMoveEnd)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      map.off('moveend', onMoveEnd)
      map.off('zoomend', onMoveEnd)
    }
  }, [map, activeLayers.hydro])

  const fallbackRiverLines = useMemo(() => {
    if (!activeLayers.hydro) return []
    const groups = new Map()

    stations.forEach((station) => {
      const river = String(station?.river || '').trim()
      if (!river || !Number.isFinite(Number(station?.lat)) || !Number.isFinite(Number(station?.lon))) return
      if (!groups.has(river)) groups.set(river, [])
      groups.get(river).push(station)
    })

    return Array.from(groups.entries())
      .map(([river, items]) => {
        const sorted = [...items].sort((a, b) => Number(a.lon) - Number(b.lon) || Number(a.lat) - Number(b.lat))
        const rank = { normal: 1, moderate: 2, high: 3 }
        const worst = sorted.reduce((acc, item) => {
          const level = String(item?.hydro_level || 'normal').toLowerCase()
          return (rank[level] || 0) > (rank[acc] || 0) ? level : acc
        }, 'normal')
        const color = sorted.find((item) => String(item?.hydro_level || 'normal').toLowerCase() === worst)?.hydro_color || '#3fb950'

        const paths = []
        if (sorted.length <= 2) {
          const directPath = sorted.map((item) => [Number(item.lat), Number(item.lon)])
          if (directPath.length >= 2) paths.push(directPath)
        } else {
          let current = []
          sorted.forEach((item) => {
            const coord = [Number(item.lat), Number(item.lon)]
            if (!current.length) {
              current = [coord]
              return
            }
            const prev = current[current.length - 1]
            const gap = Math.hypot(coord[0] - prev[0], coord[1] - prev[1])
            if (gap > 1.2) {
              if (current.length >= 2) paths.push(current)
              current = [coord]
              return
            }
            current.push(coord)
          })
          if (current.length >= 2) paths.push(current)
        }

        return {
          river,
          paths,
          color,
          weight: worst === 'high' ? 7 : worst === 'moderate' ? 6 : 5,
          level: worst,
          stations: sorted.map((item) => ({
            lat: Number(item.lat),
            lon: Number(item.lon),
            level: String(item?.hydro_level || 'normal').toLowerCase(),
            color: item?.hydro_color || color,
          })),
        }
      })
      .filter((item) => item.paths?.some((path) => path.length >= 2))
  }, [stations, activeLayers.hydro])

  const riverLines = riverGeoms.length ? riverGeoms : fallbackRiverLines

  if (!stations.length && !riverLines.length) return null

  return (
    <>
      {activeLayers.hydro && riverLines.map((riverLine) => {
        const level = String(riverLine?.level || 'normal').toLowerCase()
        const weight = Number(riverLine?.weight || (level === 'high' ? 8 : level === 'moderate' ? 6 : 5))

        return (
          <Fragment key={`river-${riverLine.river}`}>
            {(riverLine.paths || []).map((path, pathIdx) => {
              const displayPath = buildRiverRenderPath(path, `${riverLine.river}-${pathIdx}`)
              const coloredSegments = buildHydroColoredSegments(displayPath, riverLine)
              if (displayPath.length < 2) return null

              return (
                <Fragment key={`river-${riverLine.river}-${pathIdx}`}>
                  <Polyline
                    positions={displayPath}
                    smoothFactor={1.2}
                    interactive={false}
                    pathOptions={{ color: '#0b2239', opacity: 0.2, weight: weight + 9 }}
                  />
                  <Polyline
                    positions={displayPath}
                    smoothFactor={1.2}
                    interactive={false}
                    pathOptions={{ color: '#f8fbff', opacity: 0.76, weight: weight + 4.5 }}
                  />
                  {coloredSegments.map((segment, segIdx) => (
                    <Polyline
                      key={`river-segment-${riverLine.river}-${pathIdx}-${segIdx}`}
                      positions={segment.positions}
                      smoothFactor={1.1}
                      interactive={false}
                      pathOptions={{
                        color: segment.color,
                        opacity: segment.level === 'normal' ? 0.9 : 0.98,
                        weight: weight + (segment.level === 'high' ? 0.8 : segment.level === 'moderate' ? 0.4 : 0),
                        className: 'riverFlowLine',
                      }}
                    />
                  ))}
                </Fragment>
              )
            })}
          </Fragment>
        )
      })}
      {stations.map((s) => {
        const wind = Number(s?.wind_kmh || 0)
        const precip24 = Number(s?.precip_24h_mm ?? s?.precip_mm ?? 0)
        const hydroColor = s?.hydro_color || '#3fb950'
        const hydroLevel = String(s?.hydro_level || 'normal').toLowerCase()
        let color = '#58a6ff'
        let label = ''
        let radius = 6
        let haloMeters = 9000
        let haloOpacity = s?.data_source === 'estimated' ? 0.07 : 0.12

        if (activeLayers.hydro) {
          color = hydroColor
          label = s?.discharge_m3s != null ? `${Number(s.discharge_m3s).toFixed(0)} m³/s` : String(s?.hydro_level || 'normal').toUpperCase()
          radius = hydroLevel === 'high' ? 6 : hydroLevel === 'moderate' ? 5 : 0
          haloMeters = hydroLevel === 'high' ? 18000 : hydroLevel === 'moderate' ? 10000 : 0
          haloOpacity = hydroLevel === 'high' ? 0.16 : hydroLevel === 'moderate' ? 0.1 : 0
        } else if (activeLayers.pluvio || activeLayers.precip) {
          color = precip24 >= 25 ? '#f85149' : precip24 >= 8 ? '#d29922' : '#58a6ff'
          label = `${precip24.toFixed(1)} mm/24h`
          radius = Math.min(12, 5 + (precip24 / 6))
          haloMeters = Math.min(32000, 7000 + (precip24 * 550))
          haloOpacity = s?.data_source === 'estimated' ? 0.07 : 0.12
        }

        const showStationMarker = !activeLayers.hydro || hydroLevel !== 'normal'
        if (!showStationMarker) return null

        return (
          <Fragment key={`station-${s.id}`}>
            <Circle
              center={[Number(s.lat), Number(s.lon)]}
              radius={haloMeters}
              interactive={false}
              pathOptions={{ color, fillColor: color, fillOpacity: haloOpacity, weight: 0 }}
            />
            <CircleMarker
              center={[Number(s.lat), Number(s.lon)]}
              radius={radius}
              pathOptions={{ color, fillColor: color, fillOpacity: 0.75, weight: s?.data_source === 'estimated' ? 1 : 2 }}
            >
              {label && ((activeLayers.pluvio || activeLayers.precip) && map.getZoom() >= 7 || (activeLayers.hydro && map.getZoom() >= 9 && hydroLevel !== 'normal')) && (
                <Tooltip sticky direction="top" offset={[0, -6]} className="valueTooltip">
                  {label}
                </Tooltip>
              )}
              <Popup className="vigil-popup" closeButton={false} offset={[0, -8]}>
                <div className={styles.popupCard}>
                  <div className={styles.popupTitle}>{s.name || 'Stazione'}</div>
                  <div className={styles.popupLoc}>{[s.provider || s.type, s.data_source || 'live'].filter(Boolean).join(' · ')}</div>
                  <DataQualityBadge quality={s.data_quality} />
                  {activeLayers.wind && <div className={styles.popupStat}>Vento: {wind || 0} km/h</div>}
                  {(activeLayers.pluvio || activeLayers.precip) && <div className={styles.popupStat}>Pioggia 24h: {precip24.toFixed(1)} mm</div>}
                  {activeLayers.hydro && <div className={styles.popupStat}>Idrometria: {s.hydro_level || 'normal'}{s.discharge_m3s != null ? ` · ${Number(s.discharge_m3s).toFixed(0)} m³/s` : ''}</div>}
                  {s.river && <div className={styles.popupLoc}>Fiume: {s.river}</div>}
                </div>
              </Popup>
            </CircleMarker>
          </Fragment>
        )
      })}
    </>
  )
}

function LiveOverlayLayer({ activeLayers }) {
  const map = useMap()
  const [points, setPoints] = useState([])

  useEffect(() => {
    const needsOverlay = activeLayers.precip || activeLayers.pluvio
    if (!needsOverlay) {
      setPoints([])
      return
    }

    let cancelled = false
    let timer = null

    async function fetchOverlay() {
      const b = map.getBounds()
      const z = Math.round(map.getZoom())
      const url = `${API_BASE}/geo/live-overlays?min_lat=${b.getSouth()}&max_lat=${b.getNorth()}&min_lon=${b.getWest()}&max_lon=${b.getEast()}&zoom=${z}`
      try {
        const res = await fetch(url)
        if (!res.ok) return
        const data = await res.json()
        if (!cancelled) setPoints(Array.isArray(data?.points) ? data.points : [])
      } catch {
        if (!cancelled) setPoints([])
      }
    }

    fetchOverlay()
    const onMoveEnd = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(fetchOverlay, 220)
    }
    map.on('moveend', onMoveEnd)
    map.on('zoomend', onMoveEnd)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      map.off('moveend', onMoveEnd)
      map.off('zoomend', onMoveEnd)
    }
  }, [map, activeLayers.precip, activeLayers.hydro, activeLayers.pluvio])

  if (!points.length) return null

  return (
    <>
      {points.map((p, idx) => {
        const wind = Number(p?.weather?.wind_kmh || 0)
        const precip = Number(p?.weather?.precipitation_mm || 0)
        const rain = Number(p?.weather?.rain_mm || 0)
        const hydroColor = p?.hydro?.hydro_color || '#8b949e'

        let color = '#58a6ff'
        let valueText = ''
        let radius = 7
        let haloMeters = 10000
        let haloOpacity = 0.08
        const windSegments = activeLayers.wind ? buildWindSegments(p, map.getZoom()) : []

        if (activeLayers.hydro) {
          color = hydroColor
          valueText = p?.hydro?.river_discharge_max != null
            ? `Idrometria: ${p.hydro.river_discharge_max} m3/s`
            : `Indice idrometrico: ${p?.hydro?.hydro_index_estimate ?? '-'} (stima)`
          radius = Math.min(11, 6 + Number(p?.hydro?.hydro_index_estimate || 0) * 1.4)
          haloMeters = Math.min(24000, 7000 + Number(p?.hydro?.hydro_index_estimate || 0) * 5200)
          haloOpacity = 0.08
        } else if (activeLayers.pluvio || activeLayers.precip) {
          color = precip >= 8 ? '#f85149' : precip >= 2 ? '#d29922' : '#58a6ff'
          valueText = `Pioggia: ${precip || 0} mm/h`
          radius = Math.min(11, 5 + (precip / 3))
          haloMeters = Math.min(22000, 6000 + (precip * 1300))
          haloOpacity = 0.09
        } else if (activeLayers.wind) {
          color = wind >= 70 ? '#f85149' : wind >= 35 ? '#d29922' : '#58a6ff'
          valueText = `Vento: ${wind || 0} km/h`
          radius = Math.min(11, 5 + (wind / 20))
          haloMeters = Math.min(22000, 6000 + (wind * 180))
          haloOpacity = 0.08
        }

        const label = activeLayers.hydro
          ? String(p?.hydro?.hydro_level || 'normal').toUpperCase()
          : activeLayers.wind
            ? `${wind || 0} km/h`
            : `${precip || 0} mm/h`

        return (
          <Fragment key={`overlay-${idx}-${p.lat}-${p.lon}`}>
            {activeLayers.wind && windSegments.map((segment, sidx) => (
              <Polyline
                key={`wind-${idx}-${sidx}`}
                positions={segment.positions}
                interactive={false}
                pathOptions={{ color, opacity: Math.min(segment.opacity, 0.78), weight: segment.weight, dashArray: '10 14', className: 'windStreamLine' }}
              />
            ))}
            <Circle
              center={[p.lat, p.lon]}
              radius={haloMeters}
              interactive={false}
              pathOptions={{ color, fillColor: color, fillOpacity: activeLayers.wind ? Math.min(haloOpacity, 0.05) : haloOpacity, weight: 0 }}
            />
            <CircleMarker
              center={[p.lat, p.lon]}
              radius={activeLayers.wind ? Math.max(2, radius - 3) : radius}
              pathOptions={{ color, fillColor: color, fillOpacity: activeLayers.wind ? 0.18 : 0.42, weight: activeLayers.wind ? 0 : 1 }}
            >
              {map.getZoom() >= 8 && (
                <Tooltip sticky direction="top" offset={[0, -6]} className="valueTooltipMuted">
                  {label}
                </Tooltip>
              )}
              <Popup className="vigil-popup" closeButton={false} offset={[0, -8]}>
                <div className={styles.popupCard}>
                  <div className={styles.popupTitle}>Live locale</div>
                  <div className={styles.popupLoc}>{p.lat.toFixed(3)}, {p.lon.toFixed(3)}</div>
                  <div className={styles.popupStat}>{valueText}</div>
                  <div className={styles.popupLoc}>Vento: {wind || 0} km/h · Pioggia: {rain || 0} mm/h</div>
                </div>
              </Popup>
            </CircleMarker>
          </Fragment>
        )
      })}
    </>
  )
}

function MarkerPopupContent({ ev, onSelect, pos }) {
  const map = useMap()
  const category = categoryForEvent(ev)
  const meta = CATEGORY_META[category] || FALLBACK_CATEGORY
  const keyStat = keyStatForEvent(ev, meta)
  return (
    <div className={styles.popupCard}>
      <div className={styles.popupHeader}>
        <div className={styles.popupCatIcon} style={{ background: `${meta.color}26`, border: `1px solid ${meta.color}66` }}>
          <span className={styles.popupCatAbbr} style={{ color: meta.color }}>{categoryAbbr(meta, ev)}</span>
        </div>
        <div className={styles.popupTitle}>{prettyEventTitle(ev)}</div>
      </div>
      <div className={styles.popupStat}>{keyStat}</div>
      <div className={styles.popupLoc}>{[ev.region || 'Posizione sconosciuta', prettyPlatformLabel(ev.primary_platform || ev.type || '')].filter(Boolean).join(' · ')}</div>
      <div className={styles.popupBtns}>
        <button className={styles.popupBtnPrimary} onClick={() => onSelect(ev.id)}>
          Apri scheda
        </button>
        <button
          className={styles.popupBtnSec}
          onClick={() => map.flyTo([pos.lat, pos.lon], 7, { duration: 1 })}
        >
          Centra
        </button>
      </div>
    </div>
  )
}

const LAYER_BTNS = [
  { key: 'radar', label: 'Radar' },
  { key: 'satir', label: 'Sat IR' },
  { key: 'isobars', label: 'Isobare' },
  { key: 'wind', label: 'Vento' },
  { key: 'precip', label: 'Precipitaz.' },
  { key: 'pluvio', label: 'Pluvio' },
  { key: 'hydro', label: 'Idrometria' },
  { key: 'hydronet', label: 'Rete idrica' },
]

const EXCLUSIVE_METEO_LAYERS = ['wind', 'precip', 'pluvio', 'hydro']

const EMILIA_ROMAGNA_VIEW = {
  name: 'Emilia-Romagna',
  lat: 44.6,
  lon: 11.1,
  zoom: 8,
}

function LayerLegend({ activeLayers }) {
  const items = []
  let title = ''
  let sourceNote = ''

  if (activeLayers.wind) {
    title = 'Vento'
    sourceNote = 'Flusso particellare animato del vento + intensità live/stimate sul territorio.'
    items.push(['#58a6ff', '< 35 km/h'], ['#d29922', '35–69 km/h'], ['#f85149', '≥ 70 km/h'])
  } else if (activeLayers.hydro) {
    title = 'Idrometria'
    sourceNote = 'Corsi d’acqua evidenziati in base al livello idrometrico attuale.'
    items.push(['#3fb950', 'Normale'], ['#d29922', 'Moderata'], ['#f85149', 'Alta'])
  } else if (activeLayers.pluvio || activeLayers.precip) {
    title = activeLayers.pluvio ? 'Pluviometria' : 'Precipitazioni'
    sourceNote = 'Accumuli e intensità pioggia nella vista corrente.'
    items.push(['#58a6ff', 'debole'], ['#d29922', 'moderata'], ['#f85149', 'forte'])
  } else if (activeLayers.hydronet) {
    title = 'Rete idrica'
    sourceNote = 'Rete idrografica nazionale di riferimento da Natural Earth.'
  }

  if (!title && !activeLayers.isobars) return null

  return (
    <div className={styles.legendBox}>
      <div className={styles.legendTitle}>{title || 'Carte meteo'}</div>
      {items.map(([color, label]) => (
        <div key={`${color}-${label}`} className={styles.legendRow}>
          <span className={styles.legendSwatch} style={{ background: color }} />
          <span>{label}</span>
        </div>
      ))}
      {(sourceNote || activeLayers.isobars) && (
        <div className={styles.legendNote}>
          {sourceNote}
          {activeLayers.isobars ? `${sourceNote ? ' ' : ''}Carte sinottiche e isobare da fonti pubbliche.` : ''}
        </div>
      )}
    </div>
  )
}

function TerritorySummaryPanel({ activeLayers, summary, loading }) {
  const enabled = activeLayers.wind || activeLayers.precip || activeLayers.pluvio || activeLayers.hydro
  if (!enabled && !loading) return null

  const metrics = summary?.metrics || {}
  const hydro = metrics?.hydro_levels || {}
  const modeLabel = activeLayers.hydro
    ? 'Idrometria area'
    : activeLayers.pluvio
      ? 'Pluviometria area'
      : activeLayers.precip
        ? 'Precipitazioni area'
        : 'Vento area'

  return (
    <div className={styles.summaryPanel}>
      <div className={styles.summaryHeader}>
        <div className={styles.summaryTitle}>{modeLabel}</div>
        <div className={styles.summaryPlace}>{summary?.focus?.name || 'Area selezionata'}</div>
      </div>
      {loading && <div className={styles.summaryLoading}>Aggiornamento dati territoriali...</div>}
      {!loading && (
        <>
          <div className={styles.summaryGrid}>
            <div className={styles.summaryCard}><span>Eventi</span><strong>{metrics?.event_count ?? '-'}</strong></div>
            <div className={styles.summaryCard}><span>Temp. media</span><strong>{metrics?.temp_avg_c != null ? `${metrics.temp_avg_c} °C` : '-'}</strong></div>
            <div className={styles.summaryCard}><span>Vento medio</span><strong>{metrics?.wind_avg_kmh != null ? `${metrics.wind_avg_kmh} km/h` : '-'}</strong></div>
            <div className={styles.summaryCard}><span>Pioggia media</span><strong>{metrics?.precip_avg_mm != null ? `${metrics.precip_avg_mm} mm` : '-'}</strong></div>
          </div>
          <div className={styles.summaryHydro}>
            <span className={styles.summaryChipOk}>Normale {hydro.normal ?? 0}</span>
            <span className={styles.summaryChipWarn}>Moderata {hydro.moderate ?? 0}</span>
            <span className={styles.summaryChipDanger}>Alta {hydro.high ?? 0}</span>
          </div>
          {summary?.top_alerts?.[0] && (
            <div className={styles.summaryNote}>
              Top alert: {prettyEventTitle(summary.top_alerts[0])}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function SynopticPanel({ enabled }) {
  const [charts, setCharts] = useState([])

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    async function loadCharts() {
      try {
        const res = await fetch(`${API_BASE}/geo/synoptic-maps`)
        if (!res.ok) throw new Error('HTTP error')
        const data = await res.json()
        if (!cancelled) setCharts(Array.isArray(data?.charts) ? data.charts : SYNOPTIC_FALLBACK)
      } catch {
        if (!cancelled) setCharts(SYNOPTIC_FALLBACK)
      }
    }
    loadCharts()
    return () => { cancelled = true }
  }, [enabled])

  if (!enabled) return null

  const rows = charts.length ? charts : SYNOPTIC_FALLBACK
  return (
    <div className={styles.synopticPanel}>
      <div className={styles.synopticTitle}>Carte meteo · isobare</div>
      <div className={styles.synopticGrid}>
        {rows.map((chart) => (
          <a key={chart.id} href={chart.url} target="_blank" rel="noreferrer" className={styles.synopticCard}>
            <img src={chart.url} alt={chart.title} className={styles.synopticImg} loading="lazy" />
            <div className={styles.synopticCaption}>{chart.title}</div>
            <div className={styles.synopticSource}>{chart.source}</div>
          </a>
        ))}
      </div>
    </div>
  )
}

export default function VigilMap({ events, subevents = [], status = 'connecting', selectedId, selectedSubevent = null, onSelect, onSelectSubevent, onViewportChange, onGeoFocusChange, timelineRange }) {
  const [activeLayers, setActiveLayers] = useState({ radar: false, satir: false, isobars: false, wind: false, precip: false, pluvio: false, hydro: false, hydronet: false })
  const [radarUrl, setRadarUrl] = useState(null)
  const [radarTs, setRadarTs] = useState(null)
  const [geoQuery, setGeoQuery] = useState('Emilia-Romagna')
  const [searchErr, setSearchErr] = useState('')
  const [mapInstance, setMapInstance] = useState(null)
  const [didInitFocus, setDidInitFocus] = useState(false)
  const [viewZoom, setViewZoom] = useState(4)
  const [territorySummary, setTerritorySummary] = useState(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [showMapInfo, setShowMapInfo] = useState(false)

  function toggleLayer(key) {
    setActiveLayers(prev => {
      if (EXCLUSIVE_METEO_LAYERS.includes(key)) {
        const nextValue = !prev[key]
        return { ...prev, wind: false, precip: false, pluvio: false, hydro: false, [key]: nextValue }
      }
      return { ...prev, [key]: !prev[key] }
    })
  }

  useEffect(() => {
    async function loadRadar() {
      try {
        const response = await fetch('https://api.rainviewer.com/public/weather-maps.json')
        const payload = await response.json()
        const host = payload.host || 'https://tilecache.rainviewer.com'
        const frames = payload.radar?.past || []
        const latest = frames[frames.length - 1]
        if (!latest?.path) return
        setRadarUrl(`${host}${latest.path}/256/{z}/{x}/{y}/2/1_1.png`)
        setRadarTs(
          latest.time
            ? new Date(latest.time * 1000).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' })
            : null,
        )
      } catch {
        setRadarUrl(null)
      }
    }
    loadRadar()
  }, [])

  const overlayFocusMode = activeLayers.wind || activeLayers.precip || activeLayers.pluvio || activeLayers.hydro

  const markers = useMemo(() => events.map(ev => ({ ev, pos: estimateLatLon(ev) })), [events])
  const displayMarkers = useMemo(() => buildDisplayMarkers(markers, mapInstance, viewZoom), [markers, mapInstance, viewZoom])
  const visibleMarkers = useMemo(() => {
    if (!overlayFocusMode) return displayMarkers
    return displayMarkers.filter((item) => {
      if (item.kind !== 'event') return false
      const category = categoryForEvent(item.ev)
      const severity = getSeverity(item.ev?.severity)
      return Boolean((selectedId && item.ev?.id === selectedId) || category === 'wildfire' || severity === 'critical')
    })
  }, [displayMarkers, overlayFocusMode, selectedId])
  
  const markersInTimeRange = useMemo(() => {
    return visibleMarkers.filter((item) => {
      if (item.kind !== 'event') return true
      return isEventInTimeRange(item.ev, timelineRange)
    })
  }, [visibleMarkers, timelineRange])
  
  const markersOutOfTimeRange = useMemo(() => {
    return visibleMarkers.filter((item) => {
      if (item.kind !== 'event') return false
      return !isEventInTimeRange(item.ev, timelineRange)
    })
  }, [visibleMarkers, timelineRange])
  const subeventMarkers = useMemo(
    () => (subevents || []).filter(s => Number.isFinite(Number(s?.lat)) && Number.isFinite(Number(s?.lon))),
    [subevents],
  )
  const visibleSubeventMarkers = useMemo(() => {
    if (!overlayFocusMode) return subeventMarkers
    return selectedSubevent?.id ? subeventMarkers.filter((sub) => sub.id === selectedSubevent.id) : []
  }, [subeventMarkers, overlayFocusMode, selectedSubevent])

  useEffect(() => {
    if (!mapInstance || !onViewportChange) return
    const publish = () => {
      const b = mapInstance.getBounds()
      const zoom = mapInstance.getZoom()
      setViewZoom(zoom)
      onViewportChange({
        north: b.getNorth(),
        south: b.getSouth(),
        east: b.getEast(),
        west: b.getWest(),
        zoom,
      })
    }
    publish()
    mapInstance.on('moveend', publish)
    mapInstance.on('zoomend', publish)
    return () => {
      mapInstance.off('moveend', publish)
      mapInstance.off('zoomend', publish)
    }
  }, [mapInstance, onViewportChange])

  useEffect(() => {
    if (!mapInstance || didInitFocus) return
    mapInstance.flyTo([EMILIA_ROMAGNA_VIEW.lat, EMILIA_ROMAGNA_VIEW.lon], EMILIA_ROMAGNA_VIEW.zoom, { duration: 0.7 })
    if (onGeoFocusChange) {
      onGeoFocusChange({
        name: EMILIA_ROMAGNA_VIEW.name,
        lat: EMILIA_ROMAGNA_VIEW.lat,
        lon: EMILIA_ROMAGNA_VIEW.lon,
        zoom: EMILIA_ROMAGNA_VIEW.zoom,
      })
    }
    setDidInitFocus(true)
  }, [mapInstance, didInitFocus, onGeoFocusChange])

  useEffect(() => {
    if (!mapInstance || !selectedSubevent?.lat || !selectedSubevent?.lon) return
    const lat = Number(selectedSubevent.lat)
    const lon = Number(selectedSubevent.lon)
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return
    mapInstance.flyTo([lat, lon], Math.max(mapInstance.getZoom(), 10), { duration: 0.8 })
    if (onGeoFocusChange) {
      onGeoFocusChange({
        name: selectedSubevent.place_name || selectedSubevent.title || 'Sotto-evento',
        lat,
        lon,
        zoom: Math.max(mapInstance.getZoom(), 10),
      })
    }
  }, [mapInstance, onGeoFocusChange, selectedSubevent])

  useEffect(() => {
    const needsSummary = activeLayers.wind || activeLayers.precip || activeLayers.pluvio || activeLayers.hydro
    if (!mapInstance || !needsSummary) {
      setTerritorySummary(null)
      setSummaryLoading(false)
      return
    }

    let cancelled = false
    let timer = null

    async function fetchSummary() {
      const b = mapInstance.getBounds()
      const z = Math.round(mapInstance.getZoom())
      const focus = encodeURIComponent(geoQuery.trim() || 'Area selezionata')
      const url = `${API_BASE}/geo/territory-summary?min_lat=${b.getSouth()}&max_lat=${b.getNorth()}&min_lon=${b.getWest()}&max_lon=${b.getEast()}&zoom=${z}&focus_name=${focus}`
      setSummaryLoading(true)
      try {
        const res = await fetch(url)
        if (!res.ok) return
        const data = await res.json()
        if (!cancelled) setTerritorySummary(data)
      } catch {
        if (!cancelled) setTerritorySummary(null)
      } finally {
        if (!cancelled) setSummaryLoading(false)
      }
    }

    fetchSummary()
    const onMoveEnd = () => {
      if (timer) clearTimeout(timer)
      timer = setTimeout(fetchSummary, 250)
    }
    mapInstance.on('moveend', onMoveEnd)
    mapInstance.on('zoomend', onMoveEnd)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      mapInstance.off('moveend', onMoveEnd)
      mapInstance.off('zoomend', onMoveEnd)
    }
  }, [mapInstance, geoQuery, activeLayers.wind, activeLayers.precip, activeLayers.pluvio, activeLayers.hydro])

  async function zoomToQuery() {
    const q = geoQuery.trim()
    if (!q) return
    try {
      setSearchErr('')
      const res = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`)
      const data = await res.json()
      const first = Array.isArray(data) ? data[0] : null
      if (!first?.lat || !first?.lon) {
        setSearchErr('Localita non trovata')
        return
      }
      const lat = Number(first.lat)
      const lon = Number(first.lon)
      if (mapInstance && Number.isFinite(lat) && Number.isFinite(lon)) {
        mapInstance.flyTo([lat, lon], 9, { duration: 0.9 })
        if (onGeoFocusChange) onGeoFocusChange({ name: q, lat, lon, zoom: 9 })
      }
    } catch {
      setSearchErr('Ricerca non disponibile')
    }
  }

  function zoomToEmiliaRomagna() {
    if (!mapInstance) return
    mapInstance.flyTo([EMILIA_ROMAGNA_VIEW.lat, EMILIA_ROMAGNA_VIEW.lon], EMILIA_ROMAGNA_VIEW.zoom, { duration: 0.9 })
    setGeoQuery(EMILIA_ROMAGNA_VIEW.name)
    if (onGeoFocusChange) {
      onGeoFocusChange({
        name: EMILIA_ROMAGNA_VIEW.name,
        lat: EMILIA_ROMAGNA_VIEW.lat,
        lon: EMILIA_ROMAGNA_VIEW.lon,
        zoom: EMILIA_ROMAGNA_VIEW.zoom,
      })
    }
  }

  return (
    <div className={styles.wrap}>
      <MapContainer center={[50, 12]} zoom={4} minZoom={3} maxZoom={10} preferCanvas className={styles.map} whenCreated={setMapInstance}>
        <MapAutoFix />
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
          subdomains="abcd"
          attribution="&copy; OpenStreetMap &copy; CARTO"
        />
        {markersInTimeRange.map((item) => {
          if (item.kind === 'cluster') {
            const hasSelected = item.items.some(({ ev }) => ev.id === selectedId)
            return (
              <Marker
                key={item.id}
                position={[item.lat, item.lon]}
                icon={clusterIcon(item.count, hasSelected)}
                eventHandlers={{
                  click: () => {
                    if (mapInstance) mapInstance.flyTo([item.lat, item.lon], Math.min(viewZoom + 2, 9), { duration: 0.7 })
                  },
                }}
              >
                <Popup className="vigil-popup" closeButton={false} offset={[0, -14]}>
                  <div className={styles.popupCard}>
                    <div className={styles.popupTitle}>{item.count} eventi vicini</div>
                    <div className={styles.popupLoc}>Zoom automatico per separare i marker</div>
                    <div className={styles.clusterList}>
                      {item.items.slice(0, 5).map(({ ev }) => (
                        <div key={ev.id} className={styles.clusterListItem}>{prettyEventTitle(ev)}</div>
                      ))}
                      {item.count > 5 && <div className={styles.clusterListItem}>+ altri {item.count - 5}</div>}
                    </div>
                  </div>
                </Popup>
              </Marker>
            )
          }

          const { ev, pos } = item
          return (
            <Marker
              key={ev.id}
              position={[pos.lat, pos.lon]}
              icon={markerIcon(ev, selectedId)}
            >
              <Popup className="vigil-popup" closeButton={false} offset={[0, -14]}>
                <MarkerPopupContent ev={ev} onSelect={onSelect} pos={pos} />
              </Popup>
            </Marker>
          )
        })}
        {markersOutOfTimeRange.map((item) => {
          if (item.kind === 'cluster') return null
          const { ev, pos } = item
          const iconEl = markerIcon(ev, selectedId)
          const outOfRangeIcon = L.divIcon({
            className: 'vigil-marker vigil-marker-ghost',
            html: `<div style="opacity:0.25;">${iconEl.options.html}</div>`,
            iconSize: iconEl.options.iconSize,
            iconAnchor: iconEl.options.iconAnchor,
          })
          return (
            <Marker
              key={`ghost-${ev.id}`}
              position={[pos.lat, pos.lon]}
              icon={outOfRangeIcon}
              interactive={false}
            >
              <Tooltip sticky direction="top" offset={[0, -8]} opacity={0.85} permanent={false}>
                Evento fuori dal range temporale selezionato
              </Tooltip>
            </Marker>
          )
        })}
        {visibleSubeventMarkers.map((sub) => {
          const color = sub.type === 'bridge'
            ? '#f85149'
            : sub.type === 'flood'
              ? '#58a6ff'
              : sub.type === 'landslide'
                ? '#d29922'
                : sub.type === 'evacuation'
                  ? '#e3b341'
                  : '#a371f7'
          const isActive = selectedSubevent?.id === sub.id
          return (
            <CircleMarker
              key={sub.id}
              center={[Number(sub.lat), Number(sub.lon)]}
              radius={isActive ? 9 : 7}
              pathOptions={{ color, fillColor: color, fillOpacity: 0.8, weight: isActive ? 3 : 2 }}
            >
              <Popup className="vigil-popup" closeButton={false} offset={[0, -8]}>
                <div className={styles.popupCard}>
                  <div className={styles.popupHeader}>
                    <div className={styles.popupCatIcon} style={{ background: `${color}26`, border: `1px solid ${color}66` }}>
                      <span className={styles.popupCatAbbr} style={{ color }}>LOC</span>
                    </div>
                    <div className={styles.popupTitle}>{sub.title}</div>
                  </div>
                  <div className={styles.popupStat}>{sub.subcategory}</div>
                  <div className={styles.popupLoc}>{[sub.place_name || sub.region || 'Località', `${Number(sub.lat).toFixed(3)}, ${Number(sub.lon).toFixed(3)}`].join(' · ')}</div>
                  <div className={styles.popupBtns}>
                    <button className={styles.popupBtnPrimary} onClick={() => onSelectSubevent ? onSelectSubevent(sub) : onSelect(sub.parent_event_id || selectedId)}>
                      Apri locale
                    </button>
                    <button className={styles.popupBtnSec} onClick={() => mapInstance && mapInstance.flyTo([Number(sub.lat), Number(sub.lon)], 10, { duration: 0.8 })}>
                      Centra
                    </button>
                  </div>
                  <div className={styles.popupBtns} style={{ marginTop: 6 }}>
                    {sub.news_url && (
                      <button className={styles.popupBtnSec} onClick={() => window.open(sub.news_url, '_blank', 'noopener,noreferrer')}>
                        Notizia
                      </button>
                    )}
                    {sub.video_url && (
                      <button className={styles.popupBtnSec} onClick={() => window.open(sub.video_url, '_blank', 'noopener,noreferrer')}>
                        Video
                      </button>
                    )}
                  </div>
                </div>
              </Popup>
            </CircleMarker>
          )
        })}
        <RadarLayer enabled={activeLayers.radar} url={radarUrl} />
        <SatIrLayer enabled={activeLayers.satir} />
        <WindFieldLayer enabled={activeLayers.wind} />
        <LiveOverlayLayer activeLayers={activeLayers} />
        <StationsLayer activeLayers={activeLayers} />
        <HydroNetworkLayer enabled={activeLayers.hydronet} />
      </MapContainer>

      {showMapInfo && (
        <div className={styles.topLeftStack}>
          <LayerLegend activeLayers={activeLayers} />
          <TerritorySummaryPanel activeLayers={activeLayers} summary={territorySummary} loading={summaryLoading} />
        </div>
      )}
      <SynopticPanel enabled={activeLayers.isobars} />

      {/* Layer control bar */}
      <div className={styles.layerBar}>
        <div className={styles.layerBtns}>
          {LAYER_BTNS.map(({ key, label }) => (
            <button
              key={key}
              className={`${styles.layerBtn} ${activeLayers[key] ? styles.layerBtnOn : ''}`}
              onClick={() => toggleLayer(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className={styles.layerRight}>
          <div className={styles.geoSearch}>
            <button className={styles.geoBtn} onClick={() => setShowMapInfo((prev) => !prev)}>{showMapInfo ? 'Nascondi info' : 'Info layer'}</button>
            <button className={styles.geoBtnGhost} onClick={zoomToEmiliaRomagna}>Emilia-Romagna</button>
            <input
              className={styles.geoInput}
              placeholder="Regione/provincia/citta"
              value={geoQuery}
              onChange={(e) => setGeoQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') zoomToQuery() }}
            />
            <button className={styles.geoBtn} onClick={zoomToQuery}>Vai</button>
          </div>
          <span className={styles.layerAttrib}>
            RainViewer{activeLayers.radar && radarTs ? ` \xb7 ${radarTs}` : ''}
          </span>
        </div>
      </div>
      {searchErr && <div className={styles.searchErr}>{searchErr}</div>}
      {events.length === 0 && (
        <div className={styles.mapEmptyOverlay}>
          <div className={styles.mapEmptyCard}>
            <div className={styles.mapEmptyTitle}>
              {status === 'connecting' ? 'Caricamento mappa live...' : 'Nessun evento visibile'}
            </div>
            <div className={styles.mapEmptyText}>
              {status === 'connecting'
                ? 'Sto sincronizzando eventi e layer meteo dal backend.'
                : 'Prova a cambiare i filtri oppure attendi il prossimo aggiornamento automatico.'}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
