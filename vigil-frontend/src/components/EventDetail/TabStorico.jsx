import { useEffect, useMemo, useState } from 'react'
import styles from './TabStorico.module.css'

function fmt(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return Number(value).toFixed(digits)
}

function DeltaBadge({ value, unit }) {
  if (value == null) return null
  const positive = value > 0
  const color = positive ? '#f85149' : '#58a6ff'
  const sign = positive ? '+' : ''
  return (
    <span className={styles.delta} style={{ color, borderColor: `${color}55` }}>
      {sign}{fmt(value, 1)}{unit}
    </span>
  )
}

function CompareBars({ label, historical, current, anomaly, unit }) {
  const max = Math.max(Math.abs(historical || 0), Math.abs(current || 0), 1)
  const histPct = ((Math.abs(historical || 0)) / max) * 100
  const curPct = ((Math.abs(current || 0)) / max) * 100
  const curColor = anomaly > 0 ? '#f85149' : '#58a6ff'

  return (
    <div className={styles.compBlock}>
      <div className={styles.compTitle}>{label}</div>
      <div className={styles.compRow}>
        <span className={styles.compLabel}>Media</span>
        <div className={styles.compTrack}><div className={styles.compBar} style={{ width: `${histPct}%`, background: '#58a6ff' }} /></div>
        <span className={styles.compVal}>{fmt(historical)}{unit}</span>
      </div>
      <div className={styles.compRow}>
        <span className={styles.compLabel}>Attuale</span>
        <div className={styles.compTrack}><div className={styles.compBar} style={{ width: `${curPct}%`, background: curColor }} /></div>
        <span className={styles.compVal}>{fmt(current)}{unit}</span>
      </div>
    </div>
  )
}

export default function TabStorico({ eventId, lat, lon }) {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!eventId) return
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const response = await fetch(`/api/events/${eventId}/climate-context`)
        const payload = await response.json()
        if (cancelled) return
        if (!response.ok) {
          setError('fetch_error')
          setData(null)
          return
        }
        setData(payload)
      } catch {
        if (!cancelled) {
          setError('fetch_error')
          setData(null)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [eventId])

  const metricsCurrent = useMemo(() => {
    if (!data?.current_week) return []
    return [
      { label: 'Temp max', value: data.current_week.temp_max, unit: '°C' },
      { label: 'Temp min', value: data.current_week.temp_min, unit: '°C' },
      { label: 'Precipitazioni', value: data.current_week.precipitation, unit: 'mm' },
      { label: 'Vento max', value: data.current_week.windspeed_max, unit: 'km/h' },
    ]
  }, [data])

  const metricsHistorical = useMemo(() => {
    if (!data?.historical_avg) return []
    return [
      { label: 'Temp max', value: data.historical_avg.avg_temp_max, unit: '°C', delta: data?.anomalies?.anomaly_temp },
      { label: 'Temp min', value: data.historical_avg.avg_temp_min, unit: '°C', delta: null },
      { label: 'Precipitazioni', value: data.historical_avg.avg_precipitation, unit: 'mm', delta: data?.anomalies?.anomaly_precip },
      { label: 'Vento max', value: data.historical_avg.avg_windspeed_max, unit: 'km/h', delta: null },
    ]
  }, [data])

  if (loading) {
    return (
      <div className={styles.root}>
        <div className={styles.skeletonRow}>
          <div className={styles.skeletonCard} />
          <div className={styles.skeletonCard} />
          <div className={styles.skeletonCard} />
          <div className={styles.skeletonCard} />
        </div>
      </div>
    )
  }

  if (data?.error === 'no_coordinates' || lat == null || lon == null) {
    return <div className={styles.muted}>Coordinate non disponibili per questo evento</div>
  }

  if (error) {
    return <div className={styles.muted}>Contesto climatico non disponibile al momento</div>
  }

  if (!data) {
    return <div className={styles.muted}>Nessun dato climatico disponibile</div>
  }

  const tempAnomaly = data?.anomalies?.anomaly_temp
  const precipAnomaly = data?.anomalies?.anomaly_precip
  const isItaly = Boolean(data?.is_italy)

  return (
    <div className={styles.root}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
        <span style={{ fontSize: '13px', fontWeight: '500', color: '#e6edf3' }}>
          Ultimi 7 giorni
        </span>
        {isItaly && (
          <span style={{
            fontSize: '10px',
            padding: '2px 8px',
            borderRadius: '10px',
            background: 'rgba(63,185,80,0.15)',
            border: '1px solid #3fb950',
            color: '#3fb950',
            whiteSpace: 'nowrap'
          }}>
            Copertura Italia
          </span>
        )}
      </div>
      <div className={styles.metricGrid}>
        {metricsCurrent.map((m) => (
          <div key={m.label} className={styles.metricCard}>
            <div className={styles.metricLabel}>{m.label}</div>
            <div className={styles.metricVal}>
              {fmt(m.value)} <span style={{ fontSize: '11px', color: '#8b949e' }}>{m.unit}</span>
            </div>
          </div>
        ))}
      </div>

      <div className={styles.sectionHead}>Media storica - stesso mese (10 anni)</div>
      <div className={styles.metricGrid}>
        {metricsHistorical.map((m) => (
          <div key={m.label} className={styles.metricCard}>
            <div className={styles.metricLabel}>{m.label}</div>
            <div className={styles.metricVal}>
              {fmt(m.value)} <span style={{ fontSize: '11px', color: '#8b949e' }}>{m.unit}</span>
            </div>
            <DeltaBadge value={m.delta} unit={m.unit} />
          </div>
        ))}
      </div>

      <div className={styles.sectionHead}>Anomalie</div>
      <CompareBars
        label='Temperatura'
        historical={data?.historical_avg?.avg_temp_max}
        current={data?.current_week?.temp_max}
        anomaly={tempAnomaly || 0}
        unit='°C'
      />
      <CompareBars
        label='Precipitazioni'
        historical={data?.historical_avg?.avg_precipitation}
        current={data?.current_week?.precipitation}
        anomaly={precipAnomaly || 0}
        unit='mm'
      />

      <div className={styles.note}>
        Temperatura {tempAnomaly == null ? '-' : `${tempAnomaly > 0 ? '+' : ''}${fmt(tempAnomaly)}°C`} rispetto alla media storica del mese
      </div>

      {data.meteostat_station && (
        <>
          <div className={styles.sectionHead}>Stazione meteo piu vicina</div>
          <div className={styles.stationCard}>
            <div className={styles.stationLine}><span>Nome</span><strong>{data.meteostat_station.name || '-'}</strong></div>
            <div className={styles.stationLine}><span>Distanza</span><strong>{fmt(data.meteostat_station.distance_km)} km</strong></div>
            <div className={styles.stationLine}><span>Temp max</span><strong>{fmt(data.meteostat_station.monthly_normals?.temp_max)} °C</strong></div>
            <div className={styles.stationLine}><span>Temp min</span><strong>{fmt(data.meteostat_station.monthly_normals?.temp_min)} °C</strong></div>
            <div className={styles.stationLine}><span>Precipitazioni</span><strong>{fmt(data.meteostat_station.monthly_normals?.precipitation)} mm</strong></div>
            <div className={styles.stationLine}><span>Vento max</span><strong>{fmt(data.meteostat_station.monthly_normals?.windspeed_max)} km/h</strong></div>
            <div className={styles.stationSource}>Fonte: Meteostat - {data.meteostat_station.name || 'stazione'} ({fmt(data.meteostat_station.distance_km)} km)</div>
          </div>
        </>
      )}
    </div>
  )
}
