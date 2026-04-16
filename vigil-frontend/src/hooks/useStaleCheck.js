import { useEffect, useState } from 'react'

const STALE_THRESHOLDS_MS = {
  seismic:  2  * 60 * 1000,
  hydro:    10 * 60 * 1000,
  meteo:    10 * 60 * 1000,
  media:    30 * 60 * 1000,
  wildfire:  5 * 60 * 1000,
}

export function getStaleLevel(lastUpdated, category) {
  if (!lastUpdated) return 'fresh'

  const threshold = STALE_THRESHOLDS_MS[category] ?? STALE_THRESHOLDS_MS.media
  const ageMs = Date.now() - new Date(lastUpdated).getTime()
  if (!Number.isFinite(ageMs) || ageMs <= threshold) return 'fresh'
  if (ageMs <= threshold * 2) return 'mild'
  if (ageMs <= 6 * 60 * 60 * 1000) return 'moderate'
  if (ageMs <= 24 * 60 * 60 * 1000) return 'severe'
  return 'critical'
}

export function computeStaleResult(lastUpdated, category) {
  const threshold = STALE_THRESHOLDS_MS[category] ?? STALE_THRESHOLDS_MS.media
  const ageMs = Date.now() - new Date(lastUpdated).getTime()
  const ageMin = Math.floor(ageMs / 60000)

  const label = ageMin < 1
    ? 'ora'
    : ageMin < 60
      ? `${ageMin} min fa`
      : `${Math.floor(ageMin / 60)}h ${ageMin % 60}m fa`

  const isStale = ageMs > threshold
  const compactLabel = ageMin < 1
    ? 'ora'
    : ageMin < 60
      ? `${ageMin} min fa`
      : `${Math.floor(ageMin / 60)}h fa`

  const staleLevel = getStaleLevel(lastUpdated, category)

  return { isStale, ageLabel: label, compactLabel, ageMin, staleLevel }
}

export function STALE_THRESHOLD_MIN(category) {
  const ms = STALE_THRESHOLDS_MS[category] ?? STALE_THRESHOLDS_MS.media
  return Math.round(ms / 60000)
}

export default function useStaleCheck(lastUpdated, category) {
  const [result, setResult] = useState(() =>
    lastUpdated ? computeStaleResult(lastUpdated, category) : { isStale: false, ageLabel: '-', compactLabel: '-', ageMin: 0, staleLevel: 'fresh' }
  )

  useEffect(() => {
    if (!lastUpdated) {
      setResult({ isStale: false, ageLabel: '-', compactLabel: '-', ageMin: 0, staleLevel: 'fresh' })
      return
    }

    setResult(computeStaleResult(lastUpdated, category))

    const id = setInterval(() => {
      setResult(computeStaleResult(lastUpdated, category))
    }, 30_000)

    return () => clearInterval(id)
  }, [lastUpdated, category])

  return result
}
