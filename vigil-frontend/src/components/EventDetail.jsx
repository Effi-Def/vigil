import { useEffect, useMemo, useState } from 'react'
import styles from './EventDetail.module.css'
import { CATEGORY_META, FALLBACK_CATEGORY } from '../constants/categoryMeta'
import { SEVERITY_CONFIG, getSeverity } from '../constants/severity'
import TabStorico from './EventDetail/TabStorico'
import StaleBadge from './StaleBadge'
import DataQualityBadge from './DataQualityBadge'
import SeverityBadge from './SeverityBadge'

const API_BASE = import.meta.env.DEV
  ? (import.meta.env.VITE_API_TARGET || 'http://127.0.0.1:8001')
  : '/api'

const TABS = ['dati', 'notizie', 'media', 'storico', 'ai']
const TAB_LABELS = { dati: 'Dati', notizie: 'Notizie', media: 'Media', storico: 'Storico', ai: 'Sunto' }

function severityColor(raw) {
  const sev = getSeverity(raw)
  return (SEVERITY_CONFIG[sev] || SEVERITY_CONFIG.unknown).color
}

function timeAgo(iso) {
  if (!iso) return '-'
  const diff = Math.floor((Date.now() - new Date(iso)) / 60000)
  if (diff < 1) return 'ora'
  if (diff < 60) return `${diff}m fa`
  return `${Math.floor(diff / 60)}h fa`
}

function staleCategoryForEv(ev) {
  const cat = ev?.category || ev?.type || ''
  if (cat === 'earthquake' || cat === 'volcano') return 'seismic'
  if (cat === 'wildfire') return 'wildfire'
  if (cat === 'flood') return 'hydro'
  if (cat === 'cyclone' || cat === 'storm') return 'meteo'
  return 'media'
}

function domainFrom(url) {
  if (!url) return null
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return null
  }
}

function isGenericThumb(url) {
  return /google\.com\/s2\/favicons/i.test(String(url || ''))
}

function sourceLabel(item) {
  const direct = String(item?.source_name || item?.source || item?.author || item?.platform || '').trim()
  const fallback = domainFrom(item?.source_url || item?.url || item?.media_url) || 'fonte'
  return (!direct || direct.toLowerCase() === 'rss') ? fallback : direct
}

function sourceBadge(item, max = 18) {
  return sourceLabel(item).replace(/^www\./i, '').toUpperCase().slice(0, max)
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
    google_news: 'Google News',
    rss: 'Rassegna stampa',
    peertube: 'Video pubblici',
    wikimedia: 'Wikimedia',
    openverse: 'Openverse',
    usgs: 'USGS',
  }
  return labels[raw] || String(value || '')
}

function prettyTypeLabel(value) {
  return prettyPlatformLabel(String(value || '').replace(/_/g, ' '))
}

function normDegree(raw) {
  return String(raw ?? '').replace(/\xb0/g, '°')
}

function fmtNum(v, digits = 1) {
  if (v == null || Number.isNaN(Number(v))) return '-'
  return Number(v).toFixed(digits)
}

function pickField(obj, keys, fallback = null) {
  for (const key of keys) {
    const value = obj?.[key]
    if (value !== undefined && value !== null && value !== '') return value
  }
  return fallback
}

function numField(obj, keys, fallback = null) {
  const raw = pickField(obj, keys, null)
  if (raw === null) return fallback
  const n = Number(raw)
  return Number.isFinite(n) ? n : fallback
}

function textField(obj, keys, fallback = '-') {
  const raw = pickField(obj, keys, null)
  if (raw === null) return fallback
  return String(raw)
}

function canonicalCategory(ev) {
  const raw = String(ev?.category || ev?.type || 'other').toLowerCase()
  const norm = raw.replace(/\s+/g, '_')
  if (norm.includes('earthquake') || norm.includes('quake') || norm.includes('usgs')) return 'earthquake'
  if (norm.includes('flood') || norm.includes('alluv')) return 'flood'
  if (norm.includes('wildfire') || norm.includes('fire') || norm.includes('firms')) return 'wildfire'
  if (norm.includes('cyclone') || norm.includes('hurricane') || norm.includes('typhoon')) return 'cyclone'
  if (norm.includes('storm') || norm.includes('temporale') || norm.includes('nws')) return 'storm'
  if (norm.includes('volcano') || norm.includes('vulcan')) return 'volcano'
  if (norm.includes('wind')) return 'wind'
  if (norm.includes('extreme_heat') || norm.includes('heat')) return 'extreme_heat'
  if (norm.includes('extreme_cold') || norm.includes('cold')) return 'extreme_cold'
  return norm || 'other'
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

function HistoricalBar({ label, value, max, color }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  return (
    <div className={styles.barRow}>
      <span className={styles.barLabel}>{label}</span>
      <div className={styles.barTrack}>
        <div className={styles.barFill} style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className={styles.barVal}>{typeof value === 'number' ? value.toFixed(1) : (value ?? '-')}</span>
    </div>
  )
}

function MeteoCards({ ev, climateContext, title = 'METEO AREA' }) {
  const resolvedPlace = climateContext?.resolved_place
  const temp = numField(ev, ['temp_c', 'temperature_c', 'temperature', 't2m'])
  const wind = numField(ev, ['wind_kmh', 'wind_speed_kmh', 'windspeed', 'wspd'])
  const pressure = numField(ev, ['pressure_hpa', 'pressure', 'msl_pressure'])
  const precipitation = numField(ev, ['precipitation_mm', 'precipitation', 'rain_mm', 'prcp'])
  const humidity = numField(ev, ['humidity', 'relative_humidity', 'rh'])
  const cards = [
    ['Temperatura', temp != null ? `${fmtNum(temp, 1)} °C` : (climateContext?.current_week?.temp_max != null ? `${fmtNum(climateContext.current_week.temp_max, 1)} °C` : '-')],
    ['Vento max', wind != null ? `${fmtNum(wind, 0)} km/h` : (climateContext?.current_week?.windspeed_max != null ? `${fmtNum(climateContext.current_week.windspeed_max, 1)} km/h` : '-')],
    ['Pressione', pressure != null ? `${fmtNum(pressure, 0)} hPa` : '-'],
    ['Precipitazioni', precipitation != null ? `${fmtNum(precipitation, 1)} mm` : (climateContext?.current_week?.precipitation != null ? `${fmtNum(climateContext.current_week.precipitation, 1)} mm` : '-')],
    ['Umidità', humidity != null ? `${fmtNum(humidity, 0)} %` : '-'],
  ]
  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>{title}</div>
      {resolvedPlace?.name && (
        <div className={styles.histNote}>
          Riferimento locale: {resolvedPlace.name} · {resolvedPlace.source === 'title_or_region_hint' ? 'dedotto da evento/territorio' : 'coordinate evento'}
        </div>
      )}
      <div className={styles.dataGrid}>
        {cards.map(([label, value]) => (
          <div key={label} className={styles.dataCard}>
            <div className={styles.dataLabel}>{label}</div>
            <div className={styles.dataValue}>{normDegree(value)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function SismicitaArea({ ev, title = 'SISMICITÀ AREA', onData = null }) {
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [unsupported, setUnsupported] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    let cancelled = false
    async function load() {
      setUnsupported(false) // # fix: [7]
      setErrorMessage('') // # fix: [7]
      if (ev.lat == null || ev.lon == null) {
        setUnsupported(true)
        setErrorMessage('Coordinate non disponibili') // # fix: [6]
        if (onData) onData(null)
        return
      }
      setLoading(true)
      try {
        const url = `/api/events/${ev.id}/seismicity?radius_km=150&limit=10&days=365`
        const res = await fetch(url)
        if (!res.ok) {
          if (!cancelled && res.status === 422) setErrorMessage('Coordinate non disponibili') // # fix: [6]
          if (!cancelled && res.status !== 422) setErrorMessage('Errore caricamento') // # fix: [6]
          if (!cancelled) setUnsupported(true)
          if (!cancelled && onData) onData(null)
          return
        }
        const data = await res.json()
        if (!Array.isArray(data?.past_events)) {
          if (!cancelled) setUnsupported(true)
          if (!cancelled && onData) onData(data || null)
          return
        }
        const safeEvents = (data.past_events ?? []).filter(
          item => item !== null && item !== undefined && typeof item === 'object'
        ) // # fix: [8]
        const enriched = safeEvents
          .filter((item) => item.id !== ev.id)
          .slice(0, 10)
          .map((item) => {
            const distance = item.distance_km != null
              ? item.distance_km
              : ((item.lat != null && item.lon != null) ? haversineKm(ev.lat, ev.lon, item.lat, item.lon) : null)
            return { ...item, distance_km: distance }
          })
        if (!cancelled) setUnsupported(false) // # fix: [7]
        if (!cancelled) setRows(enriched)
        if (!cancelled && onData) onData(data)
      } catch {
        if (!cancelled) setErrorMessage('Errore caricamento') // # fix: [6]
        if (!cancelled) setUnsupported(true)
        if (!cancelled && onData) onData(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [ev.id, ev.lat, ev.lon])

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>{title}</div>
      {loading && <div className={styles.placeholder}>Caricamento dati INGV...</div>}
      {unsupported && <div className={styles.placeholder}>{errorMessage || 'Errore caricamento'}</div>}
      {!loading && !unsupported && rows.length === 0 && <div className={styles.empty}>Nessun evento vicino disponibile</div>}
      {!unsupported && rows.length > 0 && rows.map((item) => (
        <div key={item.id} className={styles.listRow}>
          <span className={styles.listDate}>{(item.time || item.updated_at || '').slice(0, 10) || '-'}</span>
          <span className={styles.magBadge} style={{ color: severityColor(item.severity), borderColor: `${severityColor(item.severity)}55` }}>
            M {fmtNum(item.magnitude, 1)}
          </span>
          <span className={styles.listMeta}>Prof. {fmtNum(item.depth_km, 0)} km</span>
          <span className={styles.listMeta}>{item.distance_km == null ? '-' : `${fmtNum(item.distance_km, 1)} km`}</span>
        </div>
      ))}
    </div>
  )
}

function ConfrontoStoricoClimate({ climateContext }) {
  const histTemp = climateContext?.historical_avg?.avg_temp_max
  const curTemp = climateContext?.current_week?.temp_max
  const tempAn = climateContext?.anomalies?.anomaly_temp
  const histPrec = climateContext?.historical_avg?.avg_precipitation
  const curPrec = climateContext?.current_week?.precipitation
  const precAn = climateContext?.anomalies?.anomaly_precip

  if (histTemp == null && histPrec == null) return <div className={styles.placeholder}>Contesto storico non disponibile</div>

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>CONFRONTO STORICO</div>
      {histTemp != null && curTemp != null && (
        <>
          <HistoricalBar label="Temp media" value={histTemp} max={Math.max(histTemp, curTemp, 1) * 1.2} color="#58a6ff" />
          <HistoricalBar label="Temp attuale" value={curTemp} max={Math.max(histTemp, curTemp, 1) * 1.2} color={tempAn > 0 ? '#f85149' : '#58a6ff'} />
        </>
      )}
      {histPrec != null && curPrec != null && (
        <>
          <HistoricalBar label="Prec media" value={histPrec} max={Math.max(histPrec, curPrec, 1) * 1.2} color="#58a6ff" />
          <HistoricalBar label="Prec attuale" value={curPrec} max={Math.max(histPrec, curPrec, 1) * 1.2} color={precAn > 0 ? '#f85149' : '#58a6ff'} />
        </>
      )}
    </div>
  )
}

function DatiEarthquake({ ev, climateContext }) {
  const [ingvData, setIngvData] = useState(null)
  const magnitude = numField(ev, ['magnitude', 'mag', 'm'], numField(ingvData?.current_event, ['magnitude']))
  const depth = numField(ev, ['depth_km', 'depth', 'depth_km_value'], numField(ingvData?.current_event, ['depth_km']))
  const pager = textField(ev, ['pager_alert', 'pager_level'], ({ critical: 'red', warning: 'orange', info: 'yellow' }[getSeverity(ev.severity)] || 'green'))
  const pagerColor = pager === 'red'
    ? SEVERITY_CONFIG.critical.color
    : pager === 'orange' || pager === 'yellow'
      ? SEVERITY_CONFIG.warning.color
      : SEVERITY_CONFIG.resolved.color
  const lat = Number(ev.lat) // # fix: [9]
  const lon = Number(ev.lon) // # fix: [9]
  const latStr = Number.isNaN(lat) ? 'N/D' : lat.toFixed(3) // # fix: [9]
  const lonStr = Number.isNaN(lon) ? 'N/D' : lon.toFixed(3) // # fix: [9]
  const latLon = normDegree(`${latStr}° / ${lonStr}°`) // # fix: [9]
  return (
    <>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>PARAMETRI SISMICI</div>
        <div className={styles.dataGrid}>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Magnitudo</div><div className={styles.dataValue}>{magnitude != null ? `${fmtNum(magnitude, 1)} Mw` : '-'}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Profondità</div><div className={styles.dataValue}>{depth != null ? `${fmtNum(depth, 0)} km` : '-'}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Lat/Lon</div><div className={styles.dataValue}>{latLon}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>PAGER</div><div className={styles.dataValue} style={{ color: pagerColor }}>{String(pager).toUpperCase()}</div></div>
        </div>
      </div>
      <SismicitaArea ev={ev} onData={setIngvData} />
      <details className={styles.collapse}>
        <summary className={styles.collapseTitle}>CONDIZIONI METEO</summary>
        <div className={styles.collapseBody}>
          <MeteoCards ev={ev} climateContext={climateContext} title="METEO SECONDARIO" />
        </div>
      </details>
    </>
  )
}

function DatiStorm({ ev, climateContext }) {
  const authority = textField(ev, ['authority', 'issuing_authority', 'issuer', 'source_name'], ev.primary_platform || '-')
  const validFrom = textField(ev, ['valid_from', 'valid_from_at', 'start_time'], ev.started_at?.slice(0, 16) || '-')
  const validTo = textField(ev, ['valid_to', 'valid_until', 'end_time', 'expires_at'], ev.updated_at?.slice(0, 16) || '-')
  return (
    <>
      <MeteoCards ev={ev} climateContext={climateContext} title="PARAMETRI METEO" />
      <div className={styles.section}>
        <div className={styles.sectionTitle}>ALLERTA</div>
        <div className={styles.dataGrid}>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Livello</div><div className={styles.dataValue}><SeverityBadge severity={ev.severity} size="md" showLabel /></div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Autorità</div><div className={styles.dataValue}>{authority}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Valida da</div><div className={styles.dataValue}>{validFrom}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Valida fino</div><div className={styles.dataValue}>{validTo}</div></div>
        </div>
      </div>
      <ConfrontoStoricoClimate climateContext={climateContext} />
    </>
  )
}

function DatiWildfire({ ev, climateContext }) {
  const frp = numField(ev, ['frp', 'fire_radiative_power', 'frp_mw'])
  const areaKm2 = numField(ev, ['estimated_area_km2', 'area_km2', 'burned_area_km2'])
  const sat = textField(ev, ['satellite_source', 'satellite', 'instrument'], 'MODIS/VIIRS')
  const humidity = numField(ev, ['humidity', 'relative_humidity', 'rh'])
  const temp = numField(ev, ['temp_c', 'temperature_c', 'temperature'])
  const wind = numField(ev, ['wind_kmh', 'wind_speed_kmh', 'windspeed'])
  const fireHigh = (temp != null && temp > 30) && (wind != null && wind > 20) && (humidity != null && humidity < 30)
  return (
    <>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>PARAMETRI INCENDIO</div>
        <div className={styles.dataGrid}>
          <div className={styles.dataCard}><div className={styles.dataLabel}>FRP</div><div className={styles.dataValue}>{frp != null ? `${fmtNum(frp, 1)} MW` : '-'}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Area stimata</div><div className={styles.dataValue}>{areaKm2 != null ? `${fmtNum(areaKm2, 1)} km²` : '-'}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Fonte satellite</div><div className={styles.dataValue}>{sat}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Stato</div><div className={styles.dataValue}>{ev.status || '-'}</div></div>
        </div>
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>CONDIZIONI FAVOREVOLI</div>
        <div className={styles.fireRisk} style={{ color: fireHigh ? '#f85149' : '#3fb950' }}>
          Rischio incendio: {fireHigh ? 'ALTO' : 'MODERATO'}
        </div>
      </div>
      <MeteoCards ev={ev} climateContext={climateContext} title="METEO AREA" />
    </>
  )
}

function DatiFlood({ ev, climateContext }) {
  const daily = climateContext?.current_week?.daily_precipitation || []
  const area = textField(ev, ['region', 'area', 'location'], '-')
  const source = textField(ev, ['primary_platform', 'source_name', 'platform', 'type'], '-')
  return (
    <>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>PARAMETRI EVENTO</div>
        <div className={styles.dataGrid}>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Severità</div><div className={styles.dataValue}><SeverityBadge severity={ev.severity} size="md" showLabel /></div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Area coinvolta</div><div className={styles.dataValue}>{area}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Fonte</div><div className={styles.dataValue}>{source}</div></div>
        </div>
      </div>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>PRECIPITAZIONI RECENTI</div>
        {daily.length === 0 && <div className={styles.placeholder}>Dati precipitazioni non disponibili</div>}
        {daily.length > 0 && daily.map((d) => (
          <div key={d.date} className={styles.listRow}>
            <span className={styles.listDate}>{d.date}</span>
            <span className={styles.listMeta}>{d.precipitation == null ? '-' : `${fmtNum(d.precipitation, 1)} mm`}</span>
          </div>
        ))}
      </div>
      <MeteoCards ev={ev} climateContext={climateContext} title="METEO AREA" />
    </>
  )
}

function DatiVolcano({ ev, climateContext }) {
  const alertLevel = textField(ev, ['alert_level', 'volcano_alert_level', 'status'], '-')
  const activityType = textField(ev, ['activity_type', 'volcano_activity', 'activity'], 'eruzione/fumarole/sismica')
  const ashColumn = numField(ev, ['ash_column_km', 'ash_height_km', 'ash_cloud_height_km'])
  const windDir = numField(ev, ['wind_dir_deg', 'wind_direction_deg', 'wind_direction'])
  return (
    <>
      <div className={styles.section}>
        <div className={styles.sectionTitle}>PARAMETRI VULCANICI</div>
        <div className={styles.dataGrid}>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Alert level</div><div className={styles.dataValue}>{alertLevel}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Tipo attività</div><div className={styles.dataValue}>{activityType}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Colonna cenere</div><div className={styles.dataValue}>{ashColumn != null ? `${fmtNum(ashColumn, 1)} km` : '-'}</div></div>
          <div className={styles.dataCard}><div className={styles.dataLabel}>Direzione vento</div><div className={styles.dataValue}>{windDir != null ? normDegree(`${fmtNum(windDir, 0)}°`) : '-'}</div></div>
        </div>
      </div>
      <SismicitaArea ev={ev} title="SISMICITÀ ASSOCIATA" />
      <MeteoCards ev={ev} climateContext={climateContext} title="METEO AREA" />
    </>
  )
}

function DatiGeneric({ ev, catMeta, events }) {
  const sameType = useMemo(
    () => events.filter(e => e.id !== ev.id && (e.category === ev.category || e.type === ev.type)),
    [ev, events],
  )

  const PARAMS = [
    ['Severità', (SEVERITY_CONFIG[getSeverity(ev.severity)] || SEVERITY_CONFIG.unknown).label, severityColor(ev.severity)],
    ['Tipo', ev.type || '-', null],
    ['Stato', ev.status || '-', null],
    ['Categoria', catMeta.label, catMeta.color],
    ev.magnitude != null ? ['Magnitudo', `${ev.magnitude} Mw`, null] : null,
    ev.depth_km != null ? ['Profondità', `${ev.depth_km} km`, null] : null,
  ].filter(Boolean)

  const METEO = [
    ev.temp_c != null ? ['Temperatura', `${ev.temp_c} °C`] : null,
    ev.wind_kmh != null ? ['Vento', `${ev.wind_kmh} km/h`] : null,
    ev.pressure_hpa != null ? ['Pressione', `${ev.pressure_hpa} hPa`] : null,
    ev.precipitation_mm != null ? ['Precipitazioni', `${ev.precipitation_mm} mm`] : null,
  ].filter(Boolean)

  const hasMag = ev.magnitude != null
  const hasWind = ev.wind_kmh != null
  const avgMag = sameType.length ? sameType.reduce((s, e) => s + (e.magnitude || 0), 0) / sameType.length : null
  const maxMag = sameType.length ? Math.max(...sameType.map(e => e.magnitude || 0)) : null
  const avgWind = sameType.length ? sameType.reduce((s, e) => s + (e.wind_kmh || 0), 0) / sameType.length : null
  const maxWind = sameType.length ? Math.max(...sameType.map(e => e.wind_kmh || 0)) : null
  const timelineItems = [
    {
      key: 'update',
      dot: catMeta.color,
      time: timeAgo(ev.updated_at),
      text: `Aggiornamento: ${ev.status || 'attivo'}`,
    },
    {
      key: 'first',
      dot: '#21262d',
      time: ev.started_at ? timeAgo(ev.started_at) : '-',
      text: 'Primo rilevamento',
    },
  ].filter(item => item?.text && item.text.trim() !== '' && item.text.trim() !== '-')

  return (
    <div>
      {PARAMS.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>PARAMETRI TECNICI</div>
          <div className={styles.dataGrid}>
            {PARAMS.map(([label, value, color]) => (
              <div key={label} className={styles.dataCard}>
                <div className={styles.dataLabel}>{label}</div>
                <div className={styles.dataValue} style={color ? { color } : {}}>{normDegree(value)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {METEO.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>CONDIZIONI METEO LOCALI</div>
          <div className={styles.dataGrid}>
            {METEO.map(([label, value]) => (
              <div key={label} className={styles.dataCard}>
                <div className={styles.dataLabel}>{label}</div>
                <div className={styles.dataValue}>{normDegree(value)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {sameType.length > 0 && (hasMag || hasWind) && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>CONFRONTO STORICO</div>
          {hasMag && avgMag !== null && (
            <>
              <HistoricalBar label="Media" value={avgMag} max={Math.max(avgMag, ev.magnitude || 0, 1) * 1.2} color="#8b949e" />
              <HistoricalBar label="Attuale" value={ev.magnitude} max={Math.max(avgMag, ev.magnitude || 0, 1) * 1.2} color={catMeta.color} />
            </>
          )}
          {hasWind && avgWind !== null && (
            <>
              <HistoricalBar label="Media vento" value={avgWind} max={Math.max(avgWind, ev.wind_kmh || 0, 1) * 1.2} color="#8b949e" />
              <HistoricalBar label="Vento attuale" value={ev.wind_kmh} max={Math.max(avgWind, ev.wind_kmh || 0, 1) * 1.2} color={catMeta.color} />
            </>
          )}
          <div className={styles.histNote}>
            {sameType.length} eventi registrati · max {hasMag ? `${maxMag?.toFixed(1)} Mw` : hasWind ? `${maxWind?.toFixed(0)} km/h` : '-'}
          </div>
        </div>
      )}

      <div className={styles.section}>
        <div className={styles.sectionTitle}>TIMELINE AGGIORNAMENTI</div>
        <div className={styles.timeline}>
          {timelineItems.map((item) => (
            <div key={item.key} className={styles.tlItem}>
              <div className={styles.tlDot} style={{ background: item.dot }} />
              <div className={styles.tlContent}>
                <span className={styles.tlTime}>{item.time}</span>
                <span className={styles.tlText}>{item.text}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function OperationalRelevanceBlock({ news }) {
  const scores = (news || [])
    .map((item) => Number(item?.relevance_score))
    .filter((value) => Number.isFinite(value) && value >= 0)

  if (!scores.length) return null

  const maxScore = Math.max(...scores)
  const avgScore = scores.reduce((sum, value) => sum + value, 0) / scores.length

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>TRASPARENZA FONTI</div>
      <div className={styles.dataGrid}>
        <div className={styles.dataCard}><div className={styles.dataLabel}>Relevance max</div><div className={styles.dataValue}>{maxScore.toFixed(2)}</div></div>
        <div className={styles.dataCard}><div className={styles.dataLabel}>Relevance media</div><div className={styles.dataValue}>{avgScore.toFixed(2)}</div></div>
        <div className={styles.dataCard}><div className={styles.dataLabel}>Articoli operativi</div><div className={styles.dataValue}>{scores.length}</div></div>
      </div>
    </div>
  )
}

function DatiTab({ ev, catMeta, events, climateContext, news }) {
  const category = canonicalCategory(ev)
  return (
    <>
      {category === 'earthquake' && <DatiEarthquake ev={ev} climateContext={climateContext} />}
      {['storm', 'cyclone', 'wind', 'extreme_heat', 'extreme_cold'].includes(category) && <DatiStorm ev={ev} climateContext={climateContext} />}
      {category === 'wildfire' && <DatiWildfire ev={ev} climateContext={climateContext} />}
      {category === 'flood' && <DatiFlood ev={ev} climateContext={climateContext} />}
      {category === 'volcano' && <DatiVolcano ev={ev} climateContext={climateContext} />}
      {!['earthquake', 'storm', 'cyclone', 'wind', 'extreme_heat', 'extreme_cold', 'wildfire', 'flood', 'volcano'].includes(category) && <DatiGeneric ev={ev} catMeta={catMeta} events={events} />}
      <OperationalRelevanceBlock news={news} />
    </>
  )
}

function SelectedSubeventCard({ item, onClear, onSelectSubevent }) {
  if (!item) return null
  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>FOCUS LOCALE</div>
      <div className={styles.subeventCard}>
        <div className={styles.subeventTop}>
          <span className={styles.subeventBadge}>{item.subcategory || 'Locale'}</span>
          <span className={styles.subeventMeta}>{item.place_name || item.region || 'Area evento'}</span>
        </div>
        <div className={styles.subeventTitle}>{item.title}</div>
        <div className={styles.subeventMeta}>
          {Number.isFinite(Number(item.lat)) && Number.isFinite(Number(item.lon)) ? `${Number(item.lat).toFixed(3)}, ${Number(item.lon).toFixed(3)}` : 'Coordinate non disponibili'}
        </div>
        <div className={styles.subeventLinks}>
          {item.news_url && <button className={styles.subeventBtn} onClick={() => window.open(item.news_url, '_blank', 'noopener,noreferrer')}>Notizia</button>}
          {item.video_url && <button className={styles.subeventBtn} onClick={() => window.open(item.video_url, '_blank', 'noopener,noreferrer')}>Video</button>}
          <button className={styles.subeventBtn} onClick={() => onSelectSubevent && onSelectSubevent(null)}>Chiudi focus</button>
        </div>
      </div>
    </div>
  )
}

function SubeventsBlock({ subevents, onSelectSubevent, selectedSubevent = null }) {
  if (!Array.isArray(subevents) || !subevents.length) return null

  return (
    <div className={styles.section}>
      <div className={styles.sectionTitle}>EVENTI SUL TERRITORIO</div>
      <div className={styles.sectionHint}>Impatti locali emersi dalle notizie dell’evento principale, geolocalizzati e apribili.</div>
      <div className={styles.subeventList}>
        {subevents.map((item) => (
          <div key={item.id} className={styles.subeventCard} onClick={() => onSelectSubevent && onSelectSubevent(item)} style={selectedSubevent?.id === item.id ? { borderColor: '#58a6ff', boxShadow: '0 0 0 1px rgba(88,166,255,.18) inset' } : {}}>
            <div className={styles.subeventTop}>
              <span className={styles.subeventBadge}>{item.subcategory || 'Locale'}</span>
              <span className={styles.subeventMeta}>{item.place_name || item.region || 'Area evento'}</span>
            </div>
            <div className={styles.subeventTitle}>{item.title}</div>
            <div className={styles.subeventMeta}>
              {Number.isFinite(Number(item.lat)) && Number.isFinite(Number(item.lon)) ? `${Number(item.lat).toFixed(3)}, ${Number(item.lon).toFixed(3)}` : 'Coordinate non disponibili'}
            </div>
            <div className={styles.subeventLinks}>
              {item.news_url && <button className={styles.subeventBtn} onClick={() => window.open(item.news_url, '_blank', 'noopener,noreferrer')}>Notizia</button>}
              {item.video_url && <button className={styles.subeventBtn} onClick={() => window.open(item.video_url, '_blank', 'noopener,noreferrer')}>Video</button>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function NotiziTab({ news, loading, subevents = [], selectedSubevent = null, onSelectSubevent }) {
  const articles = (news || []).filter(item => item.media_type === 'article')
  if (loading) {
    return (
      <div>
        {[1, 2, 3].map((row) => (
          <div key={row} className={styles.skeletonRow}>
            <div className={styles.skeletonLineSm} />
            <div className={styles.skeletonLineLg} />
            <div className={styles.skeletonLineMd} />
          </div>
        ))}
      </div>
    )
  }
  if (!articles.length) return <div className={styles.empty}>Nessuna notizia trovata per questo evento</div>

  const confidenceTone = (confidence) => {
    const c = Number(confidence)
    if (!Number.isFinite(c)) return '#8b949e'
    if (c > 80) return '#3fb950'
    if (c >= 50) return '#e3b341'
    return '#8b949e'
  }

  return (
    <div>
      <SelectedSubeventCard item={selectedSubevent} onSelectSubevent={onSelectSubevent} />
      <SubeventsBlock subevents={subevents} onSelectSubevent={onSelectSubevent} selectedSubevent={selectedSubevent} />
      {articles.map((item, i) => (
        <div key={i} className={styles.newsRow} onClick={() => item.url && window.open(item.url, '_blank', 'noopener,noreferrer')}>
          <div className={styles.newsSrc}>{sourceLabel(item)}</div>
          <div className={styles.newsTitle}>{item.title}</div>
          <div className={styles.newsMeta}>
            <span>{item.published ? String(item.published).slice(0, 16) : '-'}</span>
            <span className={styles.confBadge} style={{ color: confidenceTone(item.confidence ?? item.relevance), borderColor: `${confidenceTone(item.confidence ?? item.relevance)}55` }}>
              {Number.isFinite(Number(item.confidence ?? item.relevance)) ? `${Number(item.confidence ?? item.relevance)}%` : 'n/a'}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}

function MediaTab({ media, loading, event, news }) {
  if (loading) {
    return (
      <div>
        {[1, 2, 3].map((row) => (
          <div key={row} className={styles.skeletonRow}>
            <div className={styles.skeletonLineLg} />
            <div className={styles.skeletonLineMd} />
          </div>
        ))}
      </div>
    )
  }

  const images = media.filter(m => m.media_type === 'image')
  const webcams = media.filter(m => m.media_type === 'webcam')
  const videos = media.filter(m => m.media_type === 'video')
  const videoVisuals = videos.filter(m => m.thumb_url)
  const videoLinks = videos.filter(m => !m.thumb_url)
  const articleVisuals = media.filter(m => m.media_type === 'article' && m.thumb_url && !isGenericThumb(m.thumb_url))
  const liveNews = (news || []).filter(item => item.media_type === 'article')
  const liveNewsVisuals = liveNews.filter(item => item.thumb_url && !isGenericThumb(item.thumb_url))
  const liveNewsVideos = liveNews.filter(item => item.video_url)
  const hasNativeMedia = images.length > 0 || webcams.length > 0 || videos.length > 0

  if (!images.length && !webcams.length && !videos.length && !articleVisuals.length && !liveNews.length) {
    const articleCount = Number(event?.media_article_count || 0)
    if (articleCount > 0) {
      return <div className={styles.empty}>Per questo evento ci sono {articleCount} articoli, ma nessun contenuto visuale diretto disponibile.</div>
    }
    return <div className={styles.empty}>Nessun media disponibile per questo evento</div>
  }

  return (
    <div>
      {images.length > 0 && (
        <div className={styles.imgGrid}>
          {images.map(m => (
            <div key={m.id} className={styles.imgCard} onClick={() => m.media_url && window.open(m.media_url, '_blank', 'noopener,noreferrer')}>
              {m.thumb_url ? <img src={m.thumb_url} alt="" className={styles.imgThumb} /> : <div className={styles.imgPlaceholder}>IMG</div>}
              {m.platform && <div className={styles.imgSourceBadge}>{String(m.platform).toUpperCase()}</div>}
              {m.caption && <div className={styles.imgCaption}>{m.caption?.slice(0, 40)}</div>}
            </div>
          ))}
        </div>
      )}
      {videoVisuals.length > 0 && (
        <div>
          <div className={styles.sectionTitle} style={{ marginTop: 10 }}>VIDEO REALI</div>
          <div className={styles.sectionHint}>Clip pubbliche trovate online e collegate all’evento.</div>
          <div className={styles.imgGrid}>
            {videoVisuals.map(m => (
              <div key={m.id} className={styles.imgCard} onClick={() => m.media_url && window.open(m.media_url, '_blank', 'noopener,noreferrer')}>
                <div className={styles.videoCardThumbWrap}>
                  {m.thumb_url ? <img src={m.thumb_url} alt="" className={styles.imgThumb} /> : <div className={styles.imgPlaceholder}>VIDEO</div>}
                  <div className={styles.videoPlayBadge}>▶ VIDEO</div>
                </div>
                <div className={styles.imgSourceBadge}>{sourceBadge(m)}</div>
                {m.caption && <div className={styles.imgCaption}>{m.caption?.slice(0, 52)}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
      {(articleVisuals.length > 0 || liveNewsVisuals.length > 0) && (
        <div>
          <div className={styles.sectionTitle} style={{ marginTop: 10 }}>ANTEPRIME DAGLI ARTICOLI</div>
          <div className={styles.sectionHint}>
            {!hasNativeMedia ? 'Nessun media nativo disponibile: mostro anteprime editoriali correlate.' : 'Preview editoriali correlate all’evento.'}
          </div>
          <div className={styles.imgGrid}>
            {articleVisuals.map(m => (
              <div key={m.id} className={styles.imgCard} onClick={() => m.media_url && window.open(m.media_url, '_blank', 'noopener,noreferrer')}>
                {m.thumb_url ? <img src={m.thumb_url} alt="" className={styles.imgThumb} /> : <div className={styles.imgPlaceholder}>NEWS</div>}
                <div className={styles.imgSourceBadge}>{sourceBadge(m)}</div>
                {m.caption && <div className={styles.imgCaption}>{m.caption?.slice(0, 52)}</div>}
              </div>
            ))}
            {liveNewsVisuals.map((item, idx) => (
              <div key={`news-${idx}-${item.url || item.title}`} className={styles.imgCard} onClick={() => item.url && window.open(item.url, '_blank', 'noopener,noreferrer')}>
                {item.thumb_url ? <img src={item.thumb_url} alt="" className={styles.imgThumb} /> : <div className={styles.imgPlaceholder}>NEWS</div>}
                <div className={styles.imgSourceBadge}>{sourceBadge(item)}</div>
                {item.title && <div className={styles.imgCaption}>{item.title?.slice(0, 52)}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
      {liveNewsVideos.length > 0 && (
        <div className={styles.videoList}>
          <div className={styles.sectionTitle} style={{ marginTop: 10 }}>VIDEO DAGLI ARTICOLI</div>
          {liveNewsVideos.map((item, idx) => (
            <div key={`live-video-${idx}-${item.video_url || item.url}`} className={styles.videoRow} onClick={() => (item.video_url || item.url) && window.open(item.video_url || item.url, '_blank', 'noopener,noreferrer')}>
              <span>▶</span>
              <span>{item.title || item.source || 'video articolo'}</span>
            </div>
          ))}
        </div>
      )}
      {!images.length && !webcams.length && !videos.length && !articleVisuals.length && liveNews.length > 0 && (
        <div>
          <div className={styles.sectionTitle} style={{ marginTop: 10 }}>ARTICOLI RECENTI</div>
          <div className={styles.sectionHint}>Elenco testuale delle fonti correlate quando non sono disponibili foto o video nativi.</div>
          <div className={styles.videoList}>
            {liveNews.slice(0, 8).map((item, idx) => (
              <div key={`${idx}-${item.url || item.title}`} className={styles.videoRow} onClick={() => item.url && window.open(item.url, '_blank', 'noopener,noreferrer')}>
                <span>📰</span>
                <span>{item.title || item.source || 'articolo'}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {webcams.length > 0 && (
        <div className={styles.videoList}>
          <div className={styles.sectionTitle} style={{ marginTop: 10 }}>WEBCAM AREA</div>
          {webcams.map(m => (
            <div key={m.id} className={styles.webcamRow} onClick={() => m.media_url && window.open(m.media_url, '_blank', 'noopener,noreferrer')}>
              {m.thumb_url ? <img src={m.thumb_url} alt="" className={styles.webcamThumb} /> : <div className={styles.webcamThumbPlaceholder}>CAM</div>}
              <div className={styles.webcamInfo}>
                <div className={styles.webcamTitle}>{m.caption || 'Webcam live'}</div>
                <div className={styles.webcamLive}>▶ Live</div>
              </div>
            </div>
          ))}
        </div>
      )}
      {videoLinks.length > 0 && (
        <div className={styles.videoList}>
          <div className={styles.sectionTitle} style={{ marginTop: 10 }}>ALTRI LINK VIDEO</div>
          {videoLinks.map(m => (
            <div key={m.id} className={styles.videoRow} onClick={() => m.media_url && window.open(m.media_url, '_blank', 'noopener,noreferrer')}>
              <span>▶</span>
              <span>{m.caption || domainFrom(m.media_url) || m.platform || 'video'}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SummaryPreview({ summary, loading, onOpenTab }) {
  if (loading) {
    return (
      <div className={styles.summaryPreview}>
        <div className={styles.summaryPreviewTop}>
          <span className={styles.summaryPreviewLabel}>SUNTO SITUAZIONE</span>
        </div>
        <div className={styles.skeletonLineLg} />
        <div className={styles.skeletonLineMd} />
      </div>
    )
  }

  if (!summary?.summary) return null

  return (
    <div className={styles.summaryPreview}>
      <div className={styles.summaryPreviewTop}>
        <span className={styles.summaryPreviewLabel}>SUNTO SITUAZIONE</span>
        <button className={styles.summaryPreviewBtn} onClick={() => onOpenTab && onOpenTab('ai')}>
          Apri sunto
        </button>
      </div>
      <div className={styles.summaryPreviewText}>{summary.summary}</div>
      {Array.isArray(summary?.major_impacts) && summary.major_impacts.length > 0 && (
        <div className={styles.summaryImpactRow}>
          {summary.major_impacts.slice(0, 2).map((item) => (
            <span key={item} className={styles.summaryImpactChip}>{item}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function AITab({ summary, loading }) {
  if (loading) {
    return (
      <div>
        <div className={styles.aiBox}>
          <div className={styles.aiIcon}>🤖</div>
          <div className={styles.aiText}>Sto componendo il sunto dell’evento...</div>
        </div>
        {[1, 2, 3].map((row) => (
          <div key={row} className={styles.skeletonRow}>
            <div className={styles.skeletonLineLg} />
            <div className={styles.skeletonLineMd} />
          </div>
        ))}
      </div>
    )
  }

  if (!summary) {
    return (
      <div className={styles.aiBox}>
        <div className={styles.aiIcon}>🤖</div>
        <div className={styles.aiText}>Sunto non disponibile per questo evento.</div>
      </div>
    )
  }

  return (
    <div>
      <div className={styles.aiBox}>
        <div className={styles.aiLabel}>SINTESI AUTOMATICA</div>
        <div className={styles.aiHeadline}>{summary.headline}</div>
        <div className={styles.aiText}>{summary.summary}</div>
      </div>

      {Array.isArray(summary?.key_points) && summary.key_points.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>QUADRO RAPIDO</div>
          <div className={styles.aiBulletList}>
            {summary.key_points.map((item) => (
              <div key={item} className={styles.aiBulletItem}>{item}</div>
            ))}
          </div>
        </div>
      )}

      {Array.isArray(summary?.major_impacts) && summary.major_impacts.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>EVENTI MAGGIORI</div>
          <div className={styles.summaryImpactRow}>
            {summary.major_impacts.map((item) => (
              <span key={item} className={styles.summaryImpactChip}>{item}</span>
            ))}
          </div>
        </div>
      )}

      {summary?.coverage && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>COPERTURA</div>
          <div className={styles.dataGrid}>
            <div className={styles.dataCard}><div className={styles.dataLabel}>Articoli</div><div className={styles.dataValue}>{summary.coverage.articles ?? 0}</div></div>
            <div className={styles.dataCard}><div className={styles.dataLabel}>Media visivi</div><div className={styles.dataValue}>{summary.coverage.visual_media ?? 0}</div></div>
            <div className={styles.dataCard}><div className={styles.dataLabel}>Video</div><div className={styles.dataValue}>{summary.coverage.videos ?? 0}</div></div>
            <div className={styles.dataCard}><div className={styles.dataLabel}>Impatti locali</div><div className={styles.dataValue}>{summary.coverage.local_incidents ?? 0}</div></div>
          </div>
        </div>
      )}

      {Array.isArray(summary?.watch_items) && summary.watch_items.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>COSA MONITORARE</div>
          <div className={styles.aiBulletList}>
            {summary.watch_items.map((item) => (
              <div key={item} className={styles.aiBulletItem}>{item}</div>
            ))}
          </div>
        </div>
      )}

      {Array.isArray(summary?.sources) && summary.sources.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionTitle}>FONTI PRINCIPALI</div>
          <div className={styles.summaryImpactRow}>
            {summary.sources.map((item) => (
              <span key={item} className={styles.summaryImpactChip}>{item}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default function EventDetail({ events, selectedEvent, media, sources, news, subevents = [], selectedSubevent = null, onSelectSubevent, mediaLoading, newsLoading, detTab, onDetTab }) {
  const safeTab = TABS.includes(detTab) ? detTab : 'dati'
  const catMeta = selectedEvent ? (CATEGORY_META[selectedEvent.category] || FALLBACK_CATEGORY) : FALLBACK_CATEGORY
  const [climateContext, setClimateContext] = useState(null)
  const [eventSummary, setEventSummary] = useState(null)
  const [summaryLoading, setSummaryLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function loadClimate() {
      if (!selectedEvent?.id) {
        setClimateContext(null)
        return
      }
      try {
        const res = await fetch(`${API_BASE}/events/${selectedEvent.id}/climate-context`)
        const data = await res.json()
        if (!cancelled) setClimateContext(data)
      } catch {
        if (!cancelled) setClimateContext(null)
      }
    }
    loadClimate()
    return () => { cancelled = true }
  }, [selectedEvent?.id])

  useEffect(() => {
    let cancelled = false
    async function loadSummary() {
      if (!selectedEvent?.id) {
        setEventSummary(null)
        setSummaryLoading(false)
        return
      }
      setSummaryLoading(true)
      try {
        const res = await fetch(`${API_BASE}/events/${selectedEvent.id}/summary`)
        const data = await res.json()
        if (!cancelled) setEventSummary(data)
      } catch {
        if (!cancelled) setEventSummary(null)
      } finally {
        if (!cancelled) setSummaryLoading(false)
      }
    }
    loadSummary()
    return () => { cancelled = true }
  }, [selectedEvent?.id])

  const sortedMedia = useMemo(
    () => [...media].sort((a, b) => (b.quality_score || b.confidence || 0) - (a.quality_score || a.confidence || 0)),
    [media],
  )

  if (!selectedEvent) {
    return (
      <div className={styles.panel}>
        <div className={styles.emptyPanel}>
          <div style={{ fontSize: 22, marginBottom: 8, opacity: 0.3 }}>←</div>
          <div>Seleziona un evento dalla lista</div>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <div className={styles.titleBar} style={{ borderLeftColor: catMeta.color }}>
          {prettyEventTitle(selectedEvent)}
        </div>
        <div className={styles.headerMeta}>
          {[selectedEvent.region, prettyPlatformLabel(selectedEvent.primary_platform || selectedEvent.type)].filter(Boolean).join(' · ')}
        </div>
        <div className={styles.headerStale}>
          <StaleBadge lastUpdated={selectedEvent.updated_at} category={staleCategoryForEv(selectedEvent)} />
          <DataQualityBadge
            quality={selectedEvent.data_quality}
            source={selectedEvent.primary_platform || selectedEvent.type}
            size="md"
          />
        </div>
        <div className={styles.headerBadges}>
          {selectedEvent.magnitude != null && <span className={styles.hbadge}>{selectedEvent.magnitude} Mw</span>}
          {selectedEvent.wind_kmh != null && <span className={styles.hbadge}>{selectedEvent.wind_kmh} km/h</span>}
          <SeverityBadge severity={selectedEvent.severity} size="sm" showLabel />
          {selectedEvent.type && <span className={styles.hbadge}>{prettyTypeLabel(selectedEvent.type)}</span>}
          {Number(selectedEvent.media_visual_count || 0) > 0 && <span className={styles.hbadge}>VIS {Number(selectedEvent.media_visual_count || 0)}</span>}
          {Number(selectedEvent.media_article_count || 0) > 0 && <span className={styles.hbadge}>ART {Number(selectedEvent.media_article_count || 0)}</span>}
          {Array.isArray(subevents) && subevents.length > 0 && <span className={styles.hbadge}>LOC {subevents.length}</span>}
        </div>
      </div>

      <SummaryPreview summary={eventSummary} loading={summaryLoading} onOpenTab={onDetTab} />

      <div className={styles.tabbar}>
        {TABS.map(t => (
          <button key={t} className={`${styles.tab} ${safeTab === t ? styles.tabOn : ''}`} onClick={() => onDetTab(t)}>
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      <div className={styles.body}>
        {safeTab === 'dati' && <DatiTab ev={selectedEvent} catMeta={catMeta} events={events} climateContext={climateContext} news={news} />}
        {safeTab === 'notizie' && <NotiziTab news={news} loading={newsLoading} subevents={subevents} selectedSubevent={selectedSubevent} onSelectSubevent={onSelectSubevent} />}
        {safeTab === 'media' && <MediaTab media={sortedMedia} loading={mediaLoading} event={selectedEvent} news={news} />}
        {safeTab === 'storico' && <TabStorico eventId={selectedEvent.id} lat={selectedEvent.lat} lon={selectedEvent.lon} />}
        {safeTab === 'ai' && <AITab summary={eventSummary} loading={summaryLoading} />}
      </div>
    </div>
  )
}
