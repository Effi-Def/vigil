import styles from './DataQualityBadge.module.css'

const CONFIG = {
  estimated: {
    label: 'Stimato',
    cls:   'estimated',
    tip:   'Dato elaborato da modello',
  },
  synthetic: {
    label: 'Sintetico',
    cls:   'synthetic',
    tip:   'Dato simulato — non usare per decisioni operative',
  },
  unknown: {
    label: 'Qualità sconosciuta',
    cls:   'unknown',
    tip:   'Qualità del dato non dichiarata dalla fonte',
  },
}

/**
 * @param {{ quality: string|undefined, source?: string, size?: 'sm'|'md' }} props
 *   source  — nome del collector sorgente (opzionale, aggiunto al tooltip)
 *   size    — 'sm' (default, 11px) | 'md' (12px, per EventDetail header)
 */
export default function DataQualityBadge({ quality, source, size = 'sm' }) {
  const key = String(quality || '').toLowerCase()
  if (key === 'measured') return null

  const cfg = CONFIG[key] ?? CONFIG.unknown
  const tooltip = source ? `${cfg.tip} · Fonte: ${source}` : cfg.tip

  return (
    <span
      className={`${styles.badge} ${styles[cfg.cls]} ${size === 'md' ? styles.sizeMd : ''}`}
      title={tooltip}
    >
      {cfg.label}
    </span>
  )
}
