import styles from './Topbar.module.css'
import HealthRibbon from './HealthRibbon'

const STATUS_DOT = { live: '#3fb950', offline: '#f85149', mock: '#d29922', connecting: '#8b949e' }
const STATUS_LABEL = { live: 'LIVE', offline: 'OFFLINE', mock: 'MOCK', connecting: 'CONNESSIONE' }
const STATUS_PILL_STYLE = {
  live: { background: '#0d4429', color: '#3fb950', borderColor: '#238636' },
  offline: { background: '#3b1217', color: '#f85149', borderColor: '#da3633' },
  mock: { background: '#3d2f00', color: '#d29922', borderColor: '#9e6a03' },
  connecting: { background: '#161b22', color: '#8b949e', borderColor: '#30363d' },
}
const NAV = [
  { label: 'EVENTI', key: 'eventi' },
  { label: 'METEO', key: 'meteo' },
  { label: 'MEDIA', key: 'conmedia' },
  { label: 'STORICO', key: 'storico' },
]

export default function Topbar({ events, status, layers, onToggleLayer, panelMode, onPanelMode, audioAlertsEnabled = false, onAudioAlertsChange, summary = null, timelineRange = null }) {
  const dotColor = STATUS_DOT[status] || '#8b949e'
  const statusLabel = STATUS_LABEL[status] || 'CONNESSIONE'
  const pillStyle = STATUS_PILL_STYLE[status] || STATUS_PILL_STYLE.connecting
  const staleRatio = events.length > 0 ? Number(summary?.staleSevereOrCritical || 0) / events.length : 0
  const showStalePill = events.length > 0 && staleRatio > 0.5

  return (
    <nav className={styles.topbar}>
      <div className={styles.left}>
        <span className={styles.logo}>VIGIL</span>
        <div className={styles.sep} />
        {NAV.map(({ label, key }) => (
          <button
            key={key}
            className={`${styles.navItem} ${layers[key] ? styles.navOn : ''}`}
            onClick={() => onToggleLayer(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className={styles.right}>
        <div className={styles.modeSwitch}>
          <button className={`${styles.modeBtn} ${panelMode === 'eventi' ? styles.modeOn : ''}`} onClick={() => onPanelMode('eventi')}>Eventi</button>
          <button className={`${styles.modeBtn} ${panelMode === 'territorio' ? styles.modeOn : ''}`} onClick={() => onPanelMode('territorio')}>Territorio</button>
        </div>
        <span className={styles.evCount}>{events.length} eventi</span>
        {timelineRange && (
          <span className={styles.timelineRangePill} title={`Range: ${timelineRange.label}`}>
            📅 {timelineRange.label}
          </span>
        )}
        {showStalePill && <span className={styles.stalePill}>Dati datati</span>}
        <label className={styles.audioToggle} title="Attiva notifiche sonore per nuovi eventi critici">
          <input
            type="checkbox"
            checked={Boolean(audioAlertsEnabled)}
            onChange={(e) => onAudioAlertsChange && onAudioAlertsChange(e.target.checked)}
          />
          <span>Alert sonori</span>
        </label>
        <HealthRibbon />
        <span className={styles.livePill} style={pillStyle}>
          <span className={styles.liveDot} style={{ background: dotColor }} />
          {statusLabel}
        </span>
      </div>
    </nav>
  )
}
