import { useEffect, useMemo, useRef, useState } from 'react'
import useCollectorHealth from '../hooks/useCollectorHealth'
import useClickOutside from '../hooks/useClickOutside'
import styles from './HealthRibbon.module.css'

// Map display label → collector name substrings to match
const CRITICAL_SOURCES = [
  { label: 'INGV', fullName: 'Istituto Nazionale di Geofisica e Vulcanologia', match: ['ingv'] },
  { label: 'ARPA', fullName: 'Agenzia Regionale per la Protezione Ambientale', match: ['arpa'] },
  { label: 'Open-Meteo', fullName: 'Open-Meteo Weather API', match: ['open_meteo', 'openmeteo', 'open-meteo'] },
  { label: 'RSS Fire', fullName: 'Feed incendi (FIRMS / RSS)', match: ['wildfire', 'rss_wildfire', 'nasa_firms', 'firms'] },
]

const STATUS_DESCRIPTION = {
  ok: 'Operativo',
  stale: 'Dati in ritardo',
  down: 'Non raggiungibile',
  unknown: 'Stato sconosciuto',
}

const POLL_INTERVAL_SEC = 30

function findCollector(collectors, matchKeys) {
  for (const key of matchKeys) {
    const found = collectors.find(c => String(c?.name || '').toLowerCase().includes(key.toLowerCase()))
    if (found) return found
  }
  return null
}

function formatTimestamp(iso) {
  if (!iso) return 'n/d'
  try {
    return new Date(iso).toLocaleString('it-IT', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return 'n/d'
  }
}

function Dot({ source, collector, open, onToggle, buttonRef }) {
  const status = collector?.status ?? 'unknown'

  return (
    <div className={styles.dotWrap}>
      <button
        ref={buttonRef}
        type="button"
        className={`${styles.dot} ${styles[`dot_${status}`]}`}
        onClick={onToggle}
        title={source.label}
        aria-label={`${source.label}: ${status}`}
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <span className={styles.dotIndicator} />
        <span className={styles.dotLabel}>{source.label}</span>
      </button>
    </div>
  )
}

export default function HealthRibbon() {
  const collectors = useCollectorHealth()
  const [openIndex, setOpenIndex] = useState(null)
  const [popoverLeft, setPopoverLeft] = useState(0)
  const [nextRefreshAt, setNextRefreshAt] = useState(Date.now() + (POLL_INTERVAL_SEC * 1000))
  const [countdownSec, setCountdownSec] = useState(POLL_INTERVAL_SEC)
  const shellRef = useRef(null)
  const popoverRef = useRef(null)
  const buttonRefs = useRef([])

  useClickOutside(shellRef, () => {
    if (openIndex !== null) setOpenIndex(null)
  })

  const currentSource = openIndex === null ? null : CRITICAL_SOURCES[openIndex]
  const currentCollector = useMemo(
    () => (currentSource ? findCollector(collectors, currentSource.match) : null),
    [collectors, currentSource],
  )

  useEffect(() => {
    const id = setInterval(() => {
      const sec = Math.max(0, Math.ceil((nextRefreshAt - Date.now()) / 1000))
      setCountdownSec(sec)
      if (sec === 0) setNextRefreshAt(Date.now() + (POLL_INTERVAL_SEC * 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [nextRefreshAt])

  useEffect(() => {
    // Successful polls update `collectors`; keep countdown aligned to fetch cadence.
    setNextRefreshAt(Date.now() + (POLL_INTERVAL_SEC * 1000))
  }, [collectors])

  useEffect(() => {
    if (openIndex === null) return
    function updateLeft() {
      const shell = shellRef.current
      const btn = buttonRefs.current[openIndex]
      if (!shell || !btn) return
      const shellBox = shell.getBoundingClientRect()
      const btnBox = btn.getBoundingClientRect()
      setPopoverLeft(Math.max(0, btnBox.left - shellBox.left))
    }
    updateLeft()
    window.addEventListener('resize', updateLeft)
    return () => window.removeEventListener('resize', updateLeft)
  }, [openIndex])

  const openStatus = currentCollector?.status ?? 'unknown'

  return (
    <div className={styles.ribbonShell} ref={shellRef}>
      <div className={styles.ribbon}>
        {CRITICAL_SOURCES.map((source, i) => {
          const collector = findCollector(collectors, source.match)
          return (
            <Dot
              key={source.label}
              source={source}
              collector={collector}
              open={openIndex === i}
              onToggle={() => setOpenIndex(prev => prev === i ? null : i)}
              buttonRef={(node) => {
                buttonRefs.current[i] = node
              }}
            />
          )
        })}
      </div>

      {openIndex !== null && currentSource && (
        <div
          ref={popoverRef}
          className={styles.popover}
          role="dialog"
          aria-label={`${currentSource.label} stato collector`}
          style={{ '--popover-left': `${popoverLeft}px` }}
        >
          <div className={styles.popoverSource}>{`${currentSource.label} — ${currentSource.fullName}`}</div>
          <div className={`${styles.popoverStatus} ${styles[`popoverStatus_${openStatus}`]}`}>
            {STATUS_DESCRIPTION[openStatus] ?? STATUS_DESCRIPTION.unknown}
          </div>
          <div className={styles.popoverRow}>
            <span className={styles.popoverKey}>Ultimo fetch</span>
            <span className={styles.popoverVal}>{formatTimestamp(currentCollector?.lastFetch)}</span>
          </div>
          <div className={styles.popoverRow}>
            <span className={styles.popoverKey}>Latenza ultima chiamata</span>
            <span className={styles.popoverVal}>
              {Number.isFinite(Number(currentCollector?.latencyMs)) ? `${Number(currentCollector.latencyMs)}ms` : 'n/d'}
            </span>
          </div>
          <div className={styles.popoverRow}>
            <span className={styles.popoverKey}>Prossimo refresh</span>
            <span className={styles.popoverVal}>{`${countdownSec}s`}</span>
          </div>
        </div>
      )}
    </div>
  )
}
