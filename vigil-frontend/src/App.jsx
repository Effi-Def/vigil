import { useState, useMemo } from 'react'
import { useVigil } from './hooks/useVigil'
import { getSeverity } from './constants/severity'
import { getStaleLevel } from './hooks/useStaleCheck'
import useAudioAlert from './hooks/useAudioAlert'
import useTimelineFilter from './hooks/useTimelineFilter'
import Topbar from './components/Topbar'
import Map from './components/Map'
import Sidebar from './components/Sidebar'
import EventDetail from './components/EventDetail'
import TerritoryPanel from './components/TerritoryPanel'
import CriticalLane from './components/CriticalLane'
import styles from './App.module.css'
import './index.css'

const DEFAULT_LAYERS = { eventi: true, meteo: true, conmedia: false, storico: false }

const METEO_TYPES = new Set(['cyclone', 'hurricane', 'typhoon', 'storm', 'tc', 'tropical cyclone', 'tornado', 'meteoalarm', 'dpc_vigilanza'])
const ITALY_HINTS = ['italia', 'italy', 'lombardia', 'veneto', 'emilia', 'toscana', 'lazio', 'sicilia', 'puglia', 'campania', 'piemonte', 'liguria', 'calabria', 'sardegna', 'friuli']

function isItalianEvent(ev) {
  const hay = `${ev?.region || ''} ${ev?.title || ''}`.toLowerCase()
  return ITALY_HINTS.some((hint) => hay.includes(hint))
}

function staleCategoryForEv(ev) {
  const cat = ev?.category || ev?.type || ''
  if (cat === 'earthquake' || cat === 'volcano') return 'seismic'
  if (cat === 'wildfire') return 'wildfire'
  if (cat === 'flood') return 'hydro'
  if (cat === 'cyclone' || cat === 'storm') return 'meteo'
  return 'media'
}

function filterEvents(events, layers) {
  return events.filter(ev => {
    const isMeteo = METEO_TYPES.has(ev.type?.toLowerCase())
    if (isMeteo && !layers.meteo) return false
    if (!isMeteo && !layers.eventi) return false
    if (layers.conmedia && (ev.media_count || 0) === 0) return false
    if (!layers.storico) {
      const age = Date.now() - new Date(ev.updated_at).getTime()
      if (age > 7 * 24 * 3600 * 1000) return false
    }
    return true
  })
}

export default function App() {
  const [layers, setLayers] = useState(DEFAULT_LAYERS)
  const [clusterIds, setClusterIds] = useState(null)
  const [panelMode, setPanelMode] = useState('eventi')
  const [viewport, setViewport] = useState(null)
  const [geoFocus, setGeoFocus] = useState({ name: 'Emilia-Romagna', lat: 44.6, lon: 11.1, zoom: 8 })
  const [selectedSubevent, setSelectedSubevent] = useState(null)
  const [sidebarSeverityFilter, setSidebarSeverityFilter] = useState('all')
  const timeline = useTimelineFilter()
  const { events, media, sources, news, subevents, mediaLoading, newsLoading, selectedEvent, selectedId, selectEvent, status, detTab, setDetTab } = useVigil()
  const { enabled: audioAlertsEnabled, setEnabled: setAudioAlertsEnabled } = useAudioAlert(events)

  function toggleLayer(l) {
    setLayers(prev => ({ ...prev, [l]: !prev[l] }))
  }

  function handleSelect(id) {
    setClusterIds(null)
    setSelectedSubevent(null)
    selectEvent(id)
  }

  function handleSelectCluster(ids) {
    setClusterIds(ids)
    setSelectedSubevent(null)
    // Pre-select the primary (first) event so detail panel populates
    selectEvent(ids[0])
  }

  function handleSelectSubevent(subevent) {
    if (!subevent) {
      setSelectedSubevent(null)
      return
    }
    setClusterIds(null)
    setPanelMode('eventi')
    if (subevent.parent_event_id) selectEvent(subevent.parent_event_id)
    setSelectedSubevent(subevent)
    setDetTab('notizie')
  }

  const visibleEvents = useMemo(() => filterEvents(events, layers), [events, layers])
  const sidebarEvents = useMemo(() => visibleEvents, [visibleEvents])
  const dashboardSummary = useMemo(() => {
    const total = visibleEvents.length
    const critical = visibleEvents.filter((ev) => getSeverity(ev?.severity) === 'critical').length
    const wildfire = visibleEvents.filter((ev) => (ev?.category || ev?.type) === 'wildfire').length
    const withMedia = visibleEvents.filter((ev) => Number(ev?.media_visual_count || ev?.media_count || 0) > 0).length
    const italy = visibleEvents.filter(isItalianEvent).length
    const staleSevereOrCritical = visibleEvents.filter((ev) => {
      const level = getStaleLevel(ev?.last_updated || ev?.updated_at, staleCategoryForEv(ev))
      return level === 'severe' || level === 'critical'
    }).length
    return { total, critical, wildfire, withMedia, italy, staleSevereOrCritical }
  }, [visibleEvents])

  return (
    <div className={styles.appShell}>
      <Topbar
        events={visibleEvents}
        status={status}
        layers={layers}
        onToggleLayer={toggleLayer}
        panelMode={panelMode}
        onPanelMode={setPanelMode}
        audioAlertsEnabled={audioAlertsEnabled}
        onAudioAlertsChange={setAudioAlertsEnabled}
        summary={dashboardSummary}
        geoFocus={geoFocus}
        timelineRange={timeline.activeRange}
      />
      <CriticalLane
        events={visibleEvents}
        onEventSelect={(id) => { setSidebarSeverityFilter('all'); handleSelect(id) }}
        onShowAll={() => setSidebarSeverityFilter('critical')}
        timelineRange={timeline.activeRange}
      />
      <div className={styles.mainGrid}>
        <div className={styles.panelFrame}>
          <Sidebar
            events={sidebarEvents}
            subevents={subevents}
            status={status}
            selectedId={selectedId}
            selectedSubevent={selectedSubevent}
            clusterIds={clusterIds}
            onSelect={handleSelect}
            onSelectSubevent={handleSelectSubevent}
            severityFilter={sidebarSeverityFilter}
            onSeverityFilterChange={setSidebarSeverityFilter}
            summary={dashboardSummary}
            geoFocus={geoFocus}
            timeline={timeline}
          />
        </div>
        <div className={styles.mapFrame}>
          <Map
            events={visibleEvents}
            subevents={subevents}
            status={status}
            selectedId={selectedId}
            selectedSubevent={selectedSubevent}
            onSelect={handleSelect}
            onSelectSubevent={handleSelectSubevent}
            onViewportChange={setViewport}
            onGeoFocusChange={setGeoFocus}
            timelineRange={timeline.activeRange}
          />
        </div>
        <div className={styles.panelFrame}>
          {panelMode === 'territorio' ? (
            <TerritoryPanel viewport={viewport} geoFocus={geoFocus} />
          ) : (
            <EventDetail
              events={visibleEvents}
              selectedEvent={selectedEvent}
              media={media}
              sources={sources}
              news={news}
              subevents={subevents}
              selectedSubevent={selectedSubevent}
              onSelectSubevent={handleSelectSubevent}
              mediaLoading={mediaLoading}
              newsLoading={newsLoading}
              detTab={detTab}
              onDetTab={setDetTab}
            />
          )}
        </div>
      </div>
    </div>
  )
}
