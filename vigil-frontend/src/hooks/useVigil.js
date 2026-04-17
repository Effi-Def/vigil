import { useState, useEffect, useCallback, useRef } from 'react'
import { MOCK_EVENTS, MOCK_MEDIA, MOCK_SOURCES } from '../mockData'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

function buildApiBase() {
  // In local dev, go straight to the backend and avoid Vite proxy issues.
  if (import.meta.env.DEV) {
    return import.meta.env.VITE_API_TARGET || 'http://127.0.0.1:8001'
  }
  return '/api'
}

function shouldUseWebSocket() {
  // Default to HTTP polling in Vite dev to avoid noisy ws proxy ECONNRESET.
  if (import.meta.env.DEV) return import.meta.env.VITE_ENABLE_WS === 'true'
  return import.meta.env.VITE_ENABLE_WS !== 'false'
}

const API_BASE = buildApiBase()
const USE_WS = shouldUseWebSocket()

function buildWsUrl() {
  const envWsUrl = import.meta.env.VITE_WS_URL
  if (envWsUrl) return envWsUrl

  if (import.meta.env.DEV) {
    const target = import.meta.env.VITE_API_TARGET || 'http://127.0.0.1:8001'
    return `${target.replace(/^http/i, 'ws')}/ws/events`
  }

  const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProto}//${window.location.host}/api/ws/events`
}

const WS_URL = buildWsUrl()

async function apiFetch(path, timeoutMs = 8000) {
  const res = await fetch(API_BASE + path, { signal: AbortSignal.timeout(timeoutMs) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function enrichEventsWithMediaStats(baseEvents, statsMap) {
  return (baseEvents || []).map(ev => {
    const counts = statsMap.get(ev.id)
    if (!counts) return ev
    const article = counts.article || 0
    const image = counts.image || 0
    const video = counts.video || 0
    const webcam = counts.webcam || 0
    const other = counts.other || 0
    const total = counts.total || 0
    const visual = image + video + webcam
    return {
      ...ev,
      media_count: total,
      media_article_count: article,
      media_image_count: image,
      media_video_count: video,
      media_webcam_count: webcam,
      media_other_count: other,
      media_visual_count: visual,
    }
  })
}

export function useVigil() {
  const [events, setEvents] = useState([])
  const [media, setMedia] = useState([])
  const [sources, setSources] = useState([])
  const [news, setNews] = useState([])
  const [subevents, setSubevents] = useState([])
  const [mediaLoading, setMediaLoading] = useState(false)
  const [newsLoading, setNewsLoading] = useState(false)
  const [subeventsLoading, setSubeventsLoading] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [status, setStatus] = useState('connecting')
  const [detTab, setDetTab] = useState('dati')

  const selectedEvent = events.find(e => e.id === selectedId) || null
  const wsRef = useRef(null)
  const reconnectRef = useRef(null)
  const fallbackPollRef = useRef(null)
  const wsAttemptsRef = useRef(0)
  const wsConnectedRef = useRef(false)
  const unmountedRef = useRef(false)
  const mediaStatsRef = useRef(new Map())
  const mediaCacheRef = useRef(new Map())
  const newsCacheRef = useRef(new Map())
  const subeventCacheRef = useRef(new Map())
  const mediaReqRef = useRef(0)
  const newsReqRef = useRef(0)
  const subeventReqRef = useRef(0)

  const applyMediaStats = useCallback((incomingEvents) => {
    return enrichEventsWithMediaStats(incomingEvents, mediaStatsRef.current)
  }, [])

  const clearFallbackPolling = useCallback(() => {
    if (fallbackPollRef.current) {
      clearInterval(fallbackPollRef.current)
      fallbackPollRef.current = null
    }
  }, [])

  const fetchEvents = useCallback(async () => {
    const rows = await apiFetch('/events')
    setEvents(applyMediaStats(rows || []))
  }, [applyMediaStats])

  const runFallbackPoll = useCallback(async () => {
    if (USE_MOCK) return
    try {
      await fetchEvents()
      if (!wsConnectedRef.current) setStatus('polling')
    } catch {
      if (!wsConnectedRef.current) setStatus('offline')
    }
  }, [fetchEvents])

  const ensureFallbackPolling = useCallback(() => {
    if (USE_MOCK || fallbackPollRef.current) return
    runFallbackPoll()
    fallbackPollRef.current = setInterval(runFallbackPoll, 30_000)
  }, [runFallbackPoll])

  const nextReconnectDelayMs = useCallback(() => {
    wsAttemptsRef.current += 1
    if (wsAttemptsRef.current === 1) return 3000
    if (wsAttemptsRef.current === 2) return 6000
    return 15000
  }, [])

  const loadMediaRichTopItaly = useCallback(async () => {
    if (USE_MOCK) return
    try {
      const rows = await apiFetch('/events/stats/media-rich/top-italy?limit=100&min_total=1')
      const nextMap = new Map()
      for (const row of rows || []) {
        if (!row?.event_id || !row?.counts) continue
        nextMap.set(row.event_id, row.counts)
      }
      mediaStatsRef.current = nextMap
      setEvents(prev => applyMediaStats(prev))
    } catch {
      // Keep UI functional even if ranking endpoint is temporarily unavailable.
    }
  }, [applyMediaStats])

  // ── WebSocket for real-time events ──────────────────────────────────────────
  const connectWS = useCallback(() => {
    if (USE_MOCK || !USE_WS) return
    if (unmountedRef.current) return
    if (wsRef.current && wsRef.current.readyState < 2) return // already open/connecting

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      wsConnectedRef.current = true
      wsAttemptsRef.current = 0
      setStatus('live')
      clearFallbackPolling()
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current)
        reconnectRef.current = null
      }
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.type === 'events') {
          setEvents(applyMediaStats(msg.data))
          setStatus('live')
        }
      } catch {
        // ignore malformed messages
      }
    }

    ws.onerror = () => {
      if (!wsConnectedRef.current) setStatus('polling')
    }

    ws.onclose = () => {
      wsConnectedRef.current = false
      if (unmountedRef.current) return
      ensureFallbackPolling()
      setStatus('polling')
      const delayMs = nextReconnectDelayMs()
      reconnectRef.current = setTimeout(connectWS, delayMs)
    }
  }, [applyMediaStats, clearFallbackPolling, ensureFallbackPolling, nextReconnectDelayMs])

  useEffect(() => {
    unmountedRef.current = false
    if (USE_MOCK) {
      setEvents(MOCK_EVENTS)
      setStatus('mock')
      return
    }
    loadMediaRichTopItaly()
    if (USE_WS) {
      ensureFallbackPolling()
      connectWS()
    } else {
      setStatus('polling')
      ensureFallbackPolling()
    }
    const t = setInterval(loadMediaRichTopItaly, 90_000)
    return () => {
      unmountedRef.current = true
      clearInterval(t)
      clearFallbackPolling()
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      if (wsRef.current) wsRef.current.close()
    }
  }, [connectWS, loadMediaRichTopItaly, ensureFallbackPolling, clearFallbackPolling])

  useEffect(() => {
    if (!events.length) return
    const selectedStillExists = selectedId && events.some(e => e.id === selectedId)
    if (selectedStillExists) return

    const ranked = [...events].sort((a, b) => {
      const av = Number(a.media_visual_count || 0)
      const bv = Number(b.media_visual_count || 0)
      if (bv !== av) return bv - av
      const at = Number(a.media_count || 0)
      const bt = Number(b.media_count || 0)
      if (bt !== at) return bt - at
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    })

    if (ranked[0]?.id) setSelectedId(ranked[0].id)
  }, [events, selectedId])

  // ── Media ────────────────────────────────────────────────────────────────────
  const loadMedia = useCallback(async (eventId) => {
    const cached = mediaCacheRef.current.get(eventId)
    if (cached) {
      setMedia(cached)
      return
    }

    const reqId = ++mediaReqRef.current
    setMediaLoading(true)
    if (USE_MOCK) {
      const mockRows = MOCK_MEDIA[eventId] || []
      mediaCacheRef.current.set(eventId, mockRows)
      setMedia(mockRows)
      setMediaLoading(false)
      return
    }
    try {
      const data = await apiFetch(`/events/${eventId}/media?min_confidence=30&limit=50`, 12000)
      const rows = Array.isArray(data) ? data : []
      mediaCacheRef.current.set(eventId, rows)
      if (reqId === mediaReqRef.current) setMedia(rows)
    } catch {
      const fallback = MOCK_MEDIA[eventId] || []
      mediaCacheRef.current.set(eventId, fallback)
      if (reqId === mediaReqRef.current) setMedia(fallback)
    } finally {
      if (reqId === mediaReqRef.current) setMediaLoading(false)
    }
  }, [])

  // ── Sources ──────────────────────────────────────────────────────────────────
  const loadSources = useCallback(async (eventId) => {
    if (USE_MOCK) { setSources(MOCK_SOURCES[eventId] || []); return }
    try {
      const data = await apiFetch(`/events/${eventId}/sources`)
      setSources(data)
    } catch {
      setSources(MOCK_SOURCES[eventId] || [])
    }
  }, [])

  // ── News ─────────────────────────────────────────────────────────────────────
  const loadNews = useCallback(async (eventId) => {
    const cached = newsCacheRef.current.get(eventId)
    if (cached) {
      setNews(cached)
      return
    }

    const reqId = ++newsReqRef.current
    setNewsLoading(true)
    if (USE_MOCK) {
      setNews([])
      setNewsLoading(false)
      return
    }
    try {
      const data = await apiFetch(`/events/${eventId}/news`, 25000)
      const rows = Array.isArray(data) ? data : []
      newsCacheRef.current.set(eventId, rows)
      if (reqId === newsReqRef.current) setNews(rows)
    } catch {
      if (reqId === newsReqRef.current && !cached) setNews([])
    } finally {
      if (reqId === newsReqRef.current) setNewsLoading(false)
    }
  }, [])

  // ── Subevents ────────────────────────────────────────────────────────────────
  const loadSubevents = useCallback(async (eventId) => {
    const cached = subeventCacheRef.current.get(eventId)
    if (cached) {
      setSubevents(cached)
      return
    }

    const reqId = ++subeventReqRef.current
    setSubeventsLoading(true)
    if (USE_MOCK) {
      if (reqId === subeventReqRef.current) setSubevents([])
      if (reqId === subeventReqRef.current) setSubeventsLoading(false)
      return
    }
    try {
      const data = await apiFetch(`/events/${eventId}/subevents`, 18000)
      const rows = Array.isArray(data) ? data : []
      subeventCacheRef.current.set(eventId, rows)
      if (reqId === subeventReqRef.current) setSubevents(rows)
    } catch {
      if (reqId === subeventReqRef.current && !cached) setSubevents([])
    } finally {
      if (reqId === subeventReqRef.current) setSubeventsLoading(false)
    }
  }, [])

  // ── Select event ─────────────────────────────────────────────────────────────
  const selectEvent = useCallback((id) => {
    setSelectedId(id)
    setMedia(mediaCacheRef.current.get(id) || [])
    setSources([])
    setNews(newsCacheRef.current.get(id) || [])
    setSubevents(subeventCacheRef.current.get(id) || [])
  }, [])

  useEffect(() => {
    if (!selectedId) return
    loadMedia(selectedId)
    loadSubevents(selectedId)
  }, [selectedId, loadMedia, loadSubevents])

  useEffect(() => {
    if (!selectedId) return
    if (detTab === 'fonti') loadSources(selectedId)
    if (detTab === 'notizie' || detTab === 'media') loadNews(selectedId)
  }, [selectedId, detTab, loadSources, loadNews])

  return {
    events, media, sources, news, subevents,
    mediaLoading, newsLoading, subeventsLoading,
    selectedEvent, selectedId, selectEvent,
    status, detTab, setDetTab,
  }
}
