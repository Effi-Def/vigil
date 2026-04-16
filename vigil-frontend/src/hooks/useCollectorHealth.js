import { useEffect, useState } from 'react'

const API_BASE = import.meta.env.DEV
  ? (import.meta.env.VITE_API_TARGET || 'http://127.0.0.1:8001')
  : '/api'

const POLL_INTERVAL_MS = 30_000

function normalizeRecord(raw) {
  return {
    name:       String(raw?.collector || raw?.name || ''),
    status:     ['ok', 'stale', 'down'].includes(raw?.status) ? raw.status : 'unknown',
    lastFetch:  raw?.last_ok || raw?.last_run || null,
    latencyMs:  Number.isFinite(Number(raw?.latency_ms)) ? Number(raw.latency_ms) : null,
  }
}

async function fetchHealth() {
  const t0 = Date.now()
  try {
    const res = await fetch(`${API_BASE}/health/collectors`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const latencyMs = Date.now() - t0
    if (!Array.isArray(data)) return []
    return data.map(r => ({ ...normalizeRecord(r), latencyMs }))
  } catch {
    return null // signal failure
  }
}

export default function useCollectorHealth() {
  const [collectors, setCollectors] = useState([])

  useEffect(() => {
    let cancelled = false

    async function poll() {
      const result = await fetchHealth()
      if (!cancelled) {
        if (result !== null) {
          setCollectors(result)
        }
        // on failure: keep previous state (silent)
      }
    }

    poll()
    const id = setInterval(poll, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  return collectors
}
