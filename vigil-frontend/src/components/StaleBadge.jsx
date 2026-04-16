import useStaleCheck, { STALE_THRESHOLD_MIN } from '../hooks/useStaleCheck'
import styles from './StaleBadge.module.css'

export default function StaleBadge({ lastUpdated, category }) {
  const { ageLabel, compactLabel, staleLevel } = useStaleCheck(lastUpdated, category)
  const thresholdMin = STALE_THRESHOLD_MIN(category)

  if (!lastUpdated) return null

  if (staleLevel === 'fresh' || staleLevel === 'mild') {
    return (
      <span
        className={styles.freshLabel}
        title={`Soglia per questa categoria: ${thresholdMin} min`}
      >
        {compactLabel}
      </span>
    )
  }

  if (staleLevel === 'moderate') {
    return (
      <span
        className={styles.staleBadge}
        title={`Soglia per questa categoria: ${thresholdMin} min`}
      >
        Stale · {ageLabel}
      </span>
    )
  }

  if (staleLevel === 'severe') {
    return (
      <span
        className={styles.staleBadgeAged}
        title={`Soglia per questa categoria: ${thresholdMin} min`}
      >
        Stale · {compactLabel}
      </span>
    )
  }

  return (
    <span
      className={styles.staleBadgeVery}
      title="Dato potenzialmente obsoleto — verificare fonte prima dell'uso operativo"
    >
      STALE · {compactLabel}
    </span>
  )
}
