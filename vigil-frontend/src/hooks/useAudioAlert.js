import { useEffect, useRef, useState } from 'react'
import { SEVERITY_CONFIG, getSeverity } from '../constants/severity'

const STORAGE_KEY = 'vigil_audio_alerts'

function createShortBeep() {
  if (typeof window === 'undefined') return
  const Ctx = window.AudioContext || window.webkitAudioContext
  if (!Ctx) return

  const ctx = new Ctx()
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()

  osc.type = 'sine'
  osc.frequency.value = 440

  gain.gain.setValueAtTime(0.0001, ctx.currentTime)
  gain.gain.exponentialRampToValueAtTime(0.09, ctx.currentTime + 0.02)
  gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.3)

  osc.connect(gain)
  gain.connect(ctx.destination)

  osc.start()
  osc.stop(ctx.currentTime + 0.3)

  osc.onended = () => {
    try { ctx.close() } catch {}
  }
}

export default function useAudioAlert(events) {
  const [enabled, setEnabled] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })
  const seenIdsRef = useRef(new Set())
  const initializedRef = useRef(false)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(enabled))
    } catch {
      // silent fallback
    }
  }, [enabled])

  useEffect(() => {
    const rows = Array.isArray(events) ? events : []
    if (!initializedRef.current) {
      rows.forEach((ev) => {
        if (ev?.id != null) seenIdsRef.current.add(String(ev.id))
      })
      initializedRef.current = true
      return
    }

    let shouldBeep = false
    rows.forEach((ev) => {
      const id = String(ev?.id ?? '')
      if (!id) return
      const isNew = !seenIdsRef.current.has(id)
      seenIdsRef.current.add(id)
      const severity = getSeverity(ev?.severity)
      if (isNew && enabled && severity === 'critical' && SEVERITY_CONFIG.critical.audioEnabled) {
        shouldBeep = true
      }
    })

    if (shouldBeep) {
      try { createShortBeep() } catch {}
    }
  }, [events, enabled])

  return { enabled, setEnabled }
}
