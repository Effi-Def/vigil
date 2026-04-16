import { useState, useMemo, useEffect } from 'react'

const PRESETS = {
  '1h': { label: 'Ultima ora', ms: 1 * 60 * 60 * 1000 },
  '6h': { label: '6 ore', ms: 6 * 60 * 60 * 1000 },
  '24h': { label: '24 ore', ms: 24 * 60 * 60 * 1000 },
  '7d': { label: '7 giorni', ms: 7 * 24 * 60 * 60 * 1000 },
  'all': { label: 'Tutto', ms: null },
}

const DEFAULT_PRESET = '6h'
const MIN_CUSTOM_RANGE_MS = 30 * 60 * 1000 // 30 minutes

export default function useTimelineFilter() {
  const [preset, setPreset] = useState(() => {
    try {
      return window.localStorage.getItem('vigil_timeline_preset') || DEFAULT_PRESET
    } catch {
      return DEFAULT_PRESET
    }
  })

  const [customStart, setCustomStart] = useState(() => new Date(Date.now() - 24 * 60 * 60 * 1000))
  const [customEnd, setCustomEnd] = useState(() => new Date())
  const [isCustom, setIsCustom] = useState(false)

  // Persist preset to localStorage whenever it changes
  useEffect(() => {
    if (!isCustom && preset) {
      try {
        window.localStorage.setItem('vigil_timeline_preset', preset)
      } catch {
        // ignore
      }
    }
  }, [preset, isCustom])

  const activeRange = useMemo(() => {
    const now = Date.now()

    if (isCustom) {
      return {
        start: customStart.getTime(),
        end: customEnd.getTime(),
        label: `${formatDateShort(customStart)} - ${formatDateShort(customEnd)}`,
        preset: null,
        needsServerFetch: false,
      }
    }

    const presetConfig = PRESETS[preset]
    if (!presetConfig) return { start: 0, end: now, label: 'Tutto', preset: 'all', needsServerFetch: false }

    if (presetConfig.ms === null) {
      // "Tutto" preset
      return { start: 0, end: now, label: 'Tutto', preset: 'all', needsServerFetch: false }
    }

    const start = now - presetConfig.ms
    return {
      start,
      end: now,
      label: presetConfig.label,
      preset,
      needsServerFetch: false,
    }
  }, [preset, customStart, customEnd, isCustom])

  function selectPreset(presetKey) {
    if (PRESETS[presetKey]) {
      setPreset(presetKey)
      setIsCustom(false)
    }
  }

  function setCustomRange(start, end) {
    const startMs = start.getTime()
    const endMs = end.getTime()
    if (startMs >= endMs) return // invalid range

    if (endMs - startMs < MIN_CUSTOM_RANGE_MS) return // too small

    setCustomStart(start)
    setCustomEnd(end)
    setIsCustom(true)
  }

  function enableCustomMode() {
    setIsCustom(true)
  }

  function disableCustomMode() {
    setIsCustom(false)
  }

  function getServerFetchNeeded() {
    // Currently always false for frontend-only filtering
    return false
  }

  return {
    activeRange,
    selectPreset,
    setCustomRange,
    enableCustomMode,
    disableCustomMode,
    customStart,
    customEnd,
    preset,
    isCustom,
    getServerFetchNeeded,
  }
}

function formatDateShort(date) {
  const d = new Date(date)
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hour = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${day}/${month} ${hour}:${min}`
}

export function formatDateForDisplay(ms) {
  const d = new Date(ms)
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hour = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${day}/${month} ${hour}:${min}`
}

export function isEventInTimeRange(event, range) {
  if (!event || !range) return true
  const eventTs = event.last_updated || event.created_at || event.updated_at
  if (!eventTs) return true
  const ts = new Date(eventTs).getTime()
  return ts >= range.start && ts <= range.end
}
