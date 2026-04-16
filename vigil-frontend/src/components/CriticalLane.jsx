import { useMemo } from 'react'
import { CATEGORY_META, FALLBACK_CATEGORY } from '../constants/categoryMeta'
import { getSeverity } from '../constants/severity'
import { isEventInTimeRange } from '../hooks/useTimelineFilter'
import StaleBadge from './StaleBadge'
import styles from './CriticalLane.module.css'

function staleCategoryForEv(ev) {
  const cat = ev?.category || ev?.type || ''
  if (cat === 'earthquake' || cat === 'volcano') return 'seismic'
  if (cat === 'wildfire') return 'wildfire'
  if (cat === 'flood') return 'hydro'
  if (cat === 'cyclone' || cat === 'storm') return 'meteo'
  return 'media'
}

function prettyTitle(ev) {
  const raw = String(ev?.title || '').trim()
  if (!raw) return 'Evento critico'
  return raw.length > 48 ? raw.slice(0, 48) + '…' : raw
}

export default function CriticalLane({ events, onEventSelect, onShowAll, timelineRange }) {
  const critical = useMemo(() => {
    if (!Array.isArray(events) || !events.length) return []
    return events
      .filter(ev => {
        const sev = getSeverity(ev?.severity)
        const status = String(ev?.status || '').toLowerCase()
        return sev === 'critical' && status !== 'resolved'
      })
      .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
  }, [events])

  const criticalOutOfRange = useMemo(() => {
    if (!timelineRange) return 0
    return critical.filter(ev => !isEventInTimeRange(ev, timelineRange)).length
  }, [critical, timelineRange])

  if (!critical.length) return null

  const top = critical.slice(0, 3)
  const freshUnderHour = critical.filter((ev) => {
    const raw = ev?.last_updated || ev?.updated_at
    const ts = raw ? new Date(raw).getTime() : NaN
    if (!Number.isFinite(ts)) return false
    return (Date.now() - ts) < (60 * 60 * 1000)
  }).length

  return (
    <div className={styles.lane} role="alert" aria-label={`${critical.length} eventi critici attivi`}>
      <div className={styles.count}>
        <span className={styles.countNum}>{critical.length}</span>
        <span className={styles.countLabel}>{critical.length === 1 ? 'CRITICO' : 'CRITICI'}</span>
        <span className={`${styles.freshPill} ${freshUnderHour > 0 ? styles.freshPillOn : styles.freshPillOff}`}>
          {freshUnderHour} aggiornati &lt; 1h
        </span>
      </div>

      <div className={styles.sep} aria-hidden="true" />

      <div className={styles.list}>
        {top.map(ev => {
          const cat = ev?.category || ev?.type || ''
          const meta = CATEGORY_META[cat] || FALLBACK_CATEGORY
          return (
            <button
              key={ev.id}
              className={styles.item}
              onClick={() => onEventSelect?.(ev.id)}
              title={ev.title}
            >
              <span className={styles.icon} style={{ color: meta.color }}>{meta.icon}</span>
              <span className={styles.title}>{prettyTitle(ev)}</span>
              {ev.region && <span className={styles.area}>{ev.region}</span>}
              <StaleBadge lastUpdated={ev.updated_at} category={staleCategoryForEv(ev)} />
            </button>
          )
        })}
      </div>

      <button className={styles.showAll} onClick={() => onShowAll?.()}>
        Vedi tutti →
      </button>

      {criticalOutOfRange > 0 && (
        <div className={styles.outOfRangeNote}>
          {criticalOutOfRange} {criticalOutOfRange === 1 ? 'critico attivo' : 'critici attivi'} fuori dal range temporale selezionato
        </div>
      )}
    </div>
  )
}
