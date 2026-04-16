import { useEffect, useMemo, useState } from 'react'
import DataQualityBadge from './DataQualityBadge'
import styles from './TerritoryPanel.module.css'

const API_BASE = import.meta.env.DEV
  ? (import.meta.env.VITE_API_TARGET || 'http://127.0.0.1:8001')
  : '/api'

function fmt(v, unit = '') {
  if (v == null || Number.isNaN(Number(v))) return 'n/d'
  return `${Number(v).toFixed(1)}${unit}`
}

export default function TerritoryPanel({ viewport, geoFocus }) {
  const [summary, setSummary] = useState(null)
  const [stations, setStations] = useState([])
  const [loading, setLoading] = useState(false)

  const bbox = useMemo(() => {
    if (!viewport) return null
    return {
      min_lat: viewport.south,
      max_lat: viewport.north,
      min_lon: viewport.west,
      max_lon: viewport.east,
      zoom: Math.round(viewport.zoom || 7),
    }
  }, [viewport])

  useEffect(() => {
    if (!bbox) return
    let cancelled = false
    async function load() {
      setLoading(true)
      const q = `min_lat=${bbox.min_lat}&max_lat=${bbox.max_lat}&min_lon=${bbox.min_lon}&max_lon=${bbox.max_lon}&zoom=${bbox.zoom}&focus_name=${encodeURIComponent(geoFocus?.name || 'Area selezionata')}`
      try {
        const [sumRes, staRes] = await Promise.all([
          fetch(`${API_BASE}/geo/territory-summary?${q}`),
          fetch(`${API_BASE}/geo/stations?min_lat=${bbox.min_lat}&max_lat=${bbox.max_lat}&min_lon=${bbox.min_lon}&max_lon=${bbox.max_lon}&limit=120`),
        ])
        const sumJson = sumRes.ok ? await sumRes.json() : null
        const staJson = staRes.ok ? await staRes.json() : { stations: [] }
        if (!cancelled) {
          setSummary(sumJson)
          setStations(Array.isArray(staJson?.stations) ? staJson.stations : [])
        }
      } catch {
        if (!cancelled) {
          setSummary(null)
          setStations([])
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [bbox?.min_lat, bbox?.max_lat, bbox?.min_lon, bbox?.max_lon, bbox?.zoom, geoFocus?.name])

  const m = summary?.metrics || {}
  const hydro = m.hydro_levels || { high: 0, moderate: 0, normal: 0 }
  const hasMetricData = [m.event_count, m.temp_avg_c, m.wind_avg_kmh, m.precip_avg_mm].some(v => v != null && !Number.isNaN(Number(v)))
  const hasStationSignals = stations.some(s => s?.wind_kmh != null || s?.precip_mm != null || s?.hydro_level)

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <div className={styles.title}>TERRITORIO</div>
        <div className={styles.sub}>{summary?.focus?.name || geoFocus?.name || 'Area selezionata'}</div>
      </div>

      {loading && <div className={styles.loading}>Aggiornamento realtime...</div>}

      <div className={styles.grid}>
        <div className={styles.card}><span>Eventi</span><strong>{m.event_count ?? 'n/d'}</strong></div>
        <div className={styles.card}><span>Temp media</span><strong>{fmt(m.temp_avg_c, ' °C')}</strong></div>
        <div className={styles.card}><span>Vento medio</span><strong>{fmt(m.wind_avg_kmh, ' km/h')}</strong></div>
        <div className={styles.card}><span>Pioggia media</span><strong>{fmt(m.precip_avg_mm, ' mm')}</strong></div>
      </div>
      {!hasMetricData && !loading && (
        <div className={styles.note}>Per questa vista i dati meteo-idro non sono ancora abbastanza completi.</div>
      )}

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Livelli idrometrici (stima realtime)</div>
        <div className={styles.hydroRow}>
          <span className={styles.pillHigh}>Alto {hydro.high || 0}</span>
          <span className={styles.pillMid}>Moderato {hydro.moderate || 0}</span>
          <span className={styles.pillOk}>Normale {hydro.normal || 0}</span>
        </div>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Alert principali</div>
        <div className={styles.list}>
          {(summary?.top_alerts || []).map((a) => (
            <div key={a.id} className={styles.row}>
              <div className={styles.rowTitle}>{a.title}</div>
              <div className={styles.rowMeta}>{[a.region, a.severity].filter(Boolean).join(' · ')}</div>
            </div>
          ))}
          {(!summary?.top_alerts || summary.top_alerts.length === 0) && <div className={styles.empty}>Nessun alert nella vista corrente</div>}
        </div>
      </div>

      <div className={styles.section}>
        <div className={styles.sectionTitle}>Stazioni/punti meteo-idro</div>
        <div className={styles.listSmall}>
          {stations.slice(0, 18).map((s) => (
            <div key={s.id} className={styles.stationRow}>
              <div className={styles.stationTop}>
                <span>{s.name}</span>
                <DataQualityBadge quality={s.data_quality} />
              </div>
              <div className={styles.stationMeta}>{fmt(s.wind_kmh, ' km/h')} · {fmt(s.precip_mm, ' mm')}</div>
            </div>
          ))}
          {!stations.length && <div className={styles.empty}>Nessun punto disponibile per questa area</div>}
          {stations.length > 0 && !hasStationSignals && <div className={styles.empty}>Punti presenti, ma con misure realtime non ancora valorizzate.</div>}
        </div>
      </div>
    </div>
  )
}
