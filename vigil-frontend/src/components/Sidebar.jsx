import { useMemo, useState } from 'react'
import styles from './Sidebar.module.css'
import { CATEGORY_META, FALLBACK_CATEGORY } from '../constants/categoryMeta'
import StaleBadge from './StaleBadge'
import DataQualityBadge from './DataQualityBadge'
import SeverityBadge from './SeverityBadge'
import { getSeverity } from '../constants/severity'
import { isEventInTimeRange } from '../hooks/useTimelineFilter'
import TimelineFilter from './TimelineFilter'

const GROUP_WINDOW_MS = 3 * 60 * 60 * 1000
const GROUP_SUMMARY_LIMIT = 8

const KNOWN_CATS = ['earthquake', 'flood', 'wildfire', 'cyclone', 'storm', 'volcano']
const CATEGORY_CHIPS = [
  { key: 'all', label: 'Tutti', icon: null },
  { key: 'earthquake', label: 'Terremoto' },
  { key: 'flood', label: 'Alluvione' },
  { key: 'wildfire', label: 'Incendio' },
  { key: 'cyclone', label: 'Ciclone' },
  { key: 'storm', label: 'Temporale' },
  { key: 'volcano', label: 'Vulcano' },
  { key: 'altro', label: 'Altro', icon: '⚠️', color: '#8b949e' },
]

const MAJOR_COUNTRIES = [
  'Argentina', 'Australia', 'Austria', 'Belgium', 'Brazil', 'Canada', 'Chile', 'China', 'Colombia', 'Croatia',
  'Czech Republic', 'Denmark', 'Egypt', 'Finland', 'France', 'Germany', 'Greece', 'Hungary', 'India', 'Indonesia',
  'Iran', 'Iraq', 'Ireland', 'Israel', 'Italy', 'Japan', 'Kenya', 'Mexico', 'Morocco', 'Netherlands',
  'New Zealand', 'Nigeria', 'Norway', 'Pakistan', 'Peru', 'Philippines', 'Poland', 'Portugal', 'Romania', 'Russia',
  'Saudi Arabia', 'South Africa', 'South Korea', 'Spain', 'Sweden', 'Switzerland', 'Thailand', 'Turkey', 'Ukraine', 'United Kingdom',
  'United States', 'Venezuela', 'Vietnam',
]

function normalizeSearch(text) {
  return (text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

function normalizeToken(text) {
  return normalizeSearch(text).replace(/[^a-z\s]/g, ' ').replace(/\s+/g, ' ').trim()
}

function locationStringFromEvent(ev) {
  return ev.location || ev.area || ev.region || ''
}

function extractCountry(locationString) {
  const raw = (locationString || '').trim()
  if (!raw) return 'Altro'

  if (raw.includes(',')) {
    const parts = raw.split(',').map(p => p.trim()).filter(Boolean)
    const candidate = parts[parts.length - 1]
    if (candidate) return candidate
  }

  const haystack = ` ${normalizeToken(raw)} `
  for (const country of MAJOR_COUNTRIES) {
    const probe = ` ${normalizeToken(country)} `
    if (probe && haystack.includes(probe)) return country
  }

  return 'Altro'
}

function timeAgo(iso) {
  const diff = Math.floor((Date.now() - new Date(iso)) / 60000)
  if (diff < 1) return 'ora'
  if (diff < 60) return `${diff}m fa`
  return `${Math.floor(diff / 60)}h fa`
}

function categoryForEv(ev) {
  if (ev.category && CATEGORY_META[ev.category]) return ev.category
  if (ev.type && CATEGORY_META[ev.type]) return ev.type
  return null
}

function staleCategoryForEv(ev) {
  const cat = categoryForEv(ev) || ev.category || ev.type || ''
  if (cat === 'earthquake' || cat === 'volcano') return 'seismic'
  if (cat === 'wildfire') return 'wildfire'
  if (cat === 'flood') return 'hydro'
  if (cat === 'cyclone' || cat === 'storm') return 'meteo'
  return 'media'
}

function prettyEventTitle(ev) {
  const raw = String(ev?.title || '').trim()
  if (!raw) return 'Evento senza titolo'
  let title = raw
    .replace(/^Orange\s+/i, 'Allerta arancione ')
    .replace(/^Yellow\s+/i, 'Allerta gialla ')
    .replace(/^Red\s+/i, 'Allerta rossa ')
    .replace(/Warning issued for Italy\s*-\s*/i, '')
    .replace(/issued for Italy\s*-\s*/i, '')
    .replace(/Thunderstorm/gi, 'temporali')
    .replace(/Snow-ice/gi, 'neve/ghiaccio')
    .replace(/Wind/gi, 'vento')
    .replace(/Orange/gi, 'arancione')
    .replace(/Yellow/gi, 'gialla')
    .replace(/Red/gi, 'rossa')
  return title.replace(/\s+/g, ' ').trim()
}

function prettyPlatformLabel(value) {
  const raw = String(value || '').trim().toLowerCase()
  const labels = {
    meteoalarm: 'Meteoalarm',
    dpc_vigilanza: 'Protezione Civile',
    google_news: 'Google News',
    rss: 'Rassegna stampa',
    peertube: 'Video pubblici',
    wikimedia: 'Wikimedia',
    openverse: 'Openverse',
    usgs: 'USGS',
  }
  return labels[raw] || String(value || '')
}

function categoryAbbr(meta, ev) {
  const base = String(meta?.label || ev?.category || ev?.type || 'NA').trim()
  const parts = base.split(/[\s_-]+/).filter(Boolean)
  if (parts.length >= 2) return `${parts[0][0] || ''}${parts[1][0] || ''}`.toUpperCase()
  return base.slice(0, 2).toUpperCase()
}

function chipMatchesEvent(chipKey, ev) {
  if (chipKey === 'all') return true
  const cat = categoryForEv(ev)
  if (chipKey === 'altro') return !KNOWN_CATS.includes(cat)
  return cat === chipKey
}

function eventBadge(ev) {
  if (ev.is_alert) return { label: 'ALERT', color: '#f85149' }
  const age = Date.now() - new Date(ev.updated_at).getTime()
  if (age < 30 * 60000) return { label: 'NUOVO', color: '#58a6ff' }
  return null
}

function mediaSummaryBadges(ev) {
  const visual = Number(ev.media_visual_count || 0)
  const article = Number(ev.media_article_count || 0)
  const video = Number(ev.media_video_count || 0)
  const webcam = Number(ev.media_webcam_count || 0)
  const local = Number(ev.local_incident_count || 0)
  const badges = []

  if (visual > 0) badges.push({ key: 'visual', label: `VIS ${visual}`, tone: 'visual', title: 'Contenuti visuali disponibili' })
  if (article > 0) badges.push({ key: 'article', label: `ART ${article}`, tone: 'neutral', title: 'Articoli correlati' })
  if (video > 0) badges.push({ key: 'video', label: `VID ${video}`, tone: 'neutral', title: 'Video disponibili' })
  if (webcam > 0) badges.push({ key: 'webcam', label: `CAM ${webcam}`, tone: 'neutral', title: 'Webcam disponibili' })
  if (local > 0) badges.push({ key: 'local', label: `LOC ${local}`, tone: 'visual', title: 'Incidenti locali derivati dalle news' })
  if (!badges.length) badges.push({ key: 'none', label: 'NESSUN MEDIA', tone: 'empty', title: 'Nessun contenuto associato' })

  return badges
}

function eventSourceKey(ev) {
  return String(ev?.primary_platform || ev?.source_name || ev?.source || ev?.collector || 'unknown').toLowerCase()
}

function hashText(text) {
  let hash = 2166136261
  for (let idx = 0; idx < text.length; idx += 1) {
    hash ^= text.charCodeAt(idx)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(16)
}

function safeStorageGet(key) {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function safeStorageSet(key, value) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // ignore storage failures
  }
}

function groupStorageKey(hash) {
  return `vigil_group_${hash}`
}

function getEventTimestamp(ev) {
  const raw = ev?.created_at || ev?.started_at || ev?.updated_at || ev?.last_updated
  const ts = raw ? new Date(raw).getTime() : NaN
  return Number.isFinite(ts) ? ts : 0
}

function normalizedTitlePrefix(ev) {
  return normalizeSearch(prettyEventTitle(ev)).slice(0, 30)
}

function groupingTypeKey(ev) {
  const rawType = normalizeSearch(String(ev?.type || ev?.category || ''))
  const titleKey = normalizedTitlePrefix(ev)
  return rawType ? `${rawType}|${titleKey}` : titleKey
}

function isSeismicInstitutionalSource(ev) {
  const source = eventSourceKey(ev)
  return source.includes('ingv') || source.includes('usgs')
}

function shouldExcludeFromGrouping(ev, selectedId) {
  if (!ev) return true
  if (isSeismicInstitutionalSource(ev)) return true
  if (getSeverity(ev?.severity) === 'critical' && isSeismicInstitutionalSource(ev)) return true
  if (selectedId && ev.id === selectedId) return true
  if (Number(ev?.media_visual_count || 0) > 0) return true
  return false
}

function groupBucketKey(ev) {
  return `${eventSourceKey(ev)}|${groupingTypeKey(ev)}|${getSeverity(ev?.severity)}`
}

function severityRank(raw) {
  return { critical: 4, warning: 3, info: 2, unknown: 1, resolved: 0 }[getSeverity(raw)] || 0
}

function groupSeverity(events) {
  return [...events].sort((a, b) => severityRank(b?.severity) - severityRank(a?.severity))[0]?.severity || 'unknown'
}

function groupTitle(events) {
  const sample = events[0]
  const regions = new Set(events.map((ev) => String(ev?.region || ev?.area || ev?.location || 'Area').trim()).filter(Boolean))
  return `${prettyEventTitle(sample)} · ${regions.size} ${regions.size === 1 ? 'regione' : 'regioni'}`
}

function groupHash(baseKey, firstTs) {
  const stamp = new Date(firstTs || Date.now()).toISOString().slice(0, 13)
  return hashText(`${baseKey}|${stamp}`)
}

export default function Sidebar({
  events,
  subevents = [],
  status = 'connecting',
  clusterIds,
  selectedId,
  selectedSubevent = null,
  onSelect,
  onSelectSubevent,
  severityFilter = null,
  onSeverityFilterChange = null,
  timeline,
}) {
  const [query, setQuery] = useState('')
  const [catFilter, setCatFilter] = useState('all')
  const [countryFilter, setCountryFilter] = useState('all')
  const [visualOnly, setVisualOnly] = useState(false)
  const [sortMode, setSortMode] = useState('media')
  const [expandedGroups, setExpandedGroups] = useState({})
  const [severityFilterLocal, setSeverityFilterLocal] = useState('all')

  const activeSeverityFilter = severityFilter ?? severityFilterLocal

  function setSeverity(next) {
    if (severityFilter == null) setSeverityFilterLocal(next)
    if (onSeverityFilterChange) onSeverityFilterChange(next)
  }

  const baseEvents = useMemo(() => {
    if (!clusterIds?.length) return events
    const idSet = new Set(clusterIds)
    return events.filter(ev => idSet.has(ev.id))
  }, [events, clusterIds])

  const countries = useMemo(() => {
    const set = new Set()
    baseEvents.forEach(ev => {
      set.add(extractCountry(locationStringFromEvent(ev)))
    })
    return [...set].sort()
  }, [baseEvents])

  const filteredEvents = useMemo(() => {
    const tokens = normalizeSearch(query).split(/\s+/).filter(Boolean)
    return [...baseEvents]
      .filter(ev => chipMatchesEvent(catFilter, ev))
      .filter(ev => {
        if (countryFilter === 'all') return true
        return extractCountry(locationStringFromEvent(ev)) === countryFilter
      })
      .filter(ev => {
        if (!tokens.length) return true
        const hay = normalizeSearch(`${ev.title} ${ev.region} ${ev.type}`)
        return tokens.every(t => hay.includes(t))
      })
      .filter(ev => {
        if (activeSeverityFilter === 'all') return true
        return getSeverity(ev?.severity) === activeSeverityFilter
      })
      .filter(ev => {
        if (!visualOnly) return true
        return Number(ev.media_visual_count || 0) > 0
      })
      .filter(ev => isEventInTimeRange(ev, timeline.activeRange))
      .sort((a, b) => {
        if (sortMode === 'media') {
          const visualA = Number(a.media_visual_count || 0)
          const visualB = Number(b.media_visual_count || 0)
          if (visualB !== visualA) return visualB - visualA

          const mediaA = Number(a.media_count || 0)
          const mediaB = Number(b.media_count || 0)
          if (mediaB !== mediaA) return mediaB - mediaA
        }

        const rank = { critical: 4, warning: 3, info: 2, unknown: 1, resolved: 0 }
        const r = (rank[getSeverity(b.severity)] || 0) - (rank[getSeverity(a.severity)] || 0)
        if (r !== 0) return r
        return new Date(b.updated_at) - new Date(a.updated_at)
      })
  }, [baseEvents, catFilter, countryFilter, query, activeSeverityFilter, visualOnly, sortMode, timeline.activeRange])

  const groupedEntries = useMemo(() => {
    const withIndex = filteredEvents.map((ev, index) => ({ ev, index }))
    const standalone = []
    const buckets = new Map()

    for (const item of withIndex) {
      if (shouldExcludeFromGrouping(item.ev, selectedId)) {
        standalone.push({ kind: 'event', ev: item.ev, order: item.index })
        continue
      }

      const key = groupBucketKey(item.ev)
      if (!buckets.has(key)) buckets.set(key, [])
      buckets.get(key).push(item)
    }

    const groups = []

    for (const [key, bucketItems] of buckets.entries()) {
      const byTime = [...bucketItems].sort((a, b) => getEventTimestamp(a.ev) - getEventTimestamp(b.ev))
      let current = []
      let currentStart = 0

      const flush = () => {
        if (!current.length) return
        const members = [...current].sort((a, b) => a.index - b.index)
        if (members.length === 1) {
          standalone.push({ kind: 'event', ev: members[0].ev, order: members[0].index })
        } else {
          const firstTs = getEventTimestamp(members[0].ev)
          const hash = groupHash(key, firstTs)
          const storageKey = groupStorageKey(hash)
          const persistedExpanded = safeStorageGet(storageKey) === '1'
          const expanded = expandedGroups[hash] ?? persistedExpanded
          groups.push({
            kind: 'group',
            key: hash,
            storageKey,
            bucketKey: key,
            order: Math.min(...members.map((member) => member.index)),
            members: members.map((member) => member.ev),
            expanded,
          })
        }
        current = []
        currentStart = 0
      }

      for (const item of byTime) {
        const ts = getEventTimestamp(item.ev)
        if (!current.length) {
          current = [item]
          currentStart = ts
          continue
        }
        if ((ts - currentStart) > GROUP_WINDOW_MS) {
          flush()
          current = [item]
          currentStart = ts
          continue
        }
        current.push(item)
      }

      flush()
    }

    return [...standalone, ...groups].sort((a, b) => a.order - b.order)
  }, [filteredEvents, expandedGroups, selectedId])

  const activeGroupCount = useMemo(
    () => groupedEntries.filter((entry) => entry.kind === 'group').length,
    [groupedEntries],
  )

  function toggleGroup(hash, storageKey) {
    setExpandedGroups((prev) => {
      const nextValue = !(prev[hash] ?? (safeStorageGet(storageKey) === '1'))
      safeStorageSet(storageKey, nextValue ? '1' : '0')
      return { ...prev, [hash]: nextValue }
    })
  }

  function renderEventRow(ev) {
    const cat = categoryForEv(ev)
    const meta = (cat && CATEGORY_META[cat]) || FALLBACK_CATEGORY
    const badge = eventBadge(ev)
    const isSelected = selectedId === ev.id
    const platform = prettyPlatformLabel(ev.primary_platform || ev.type || '')
    const mediaBadges = mediaSummaryBadges(ev)
    const localItems = isSelected ? (subevents || []).slice(0, 6) : []

    return (
      <div key={ev.id}>
        <div
          className={`${styles.row} ${isSelected ? styles.rowSel : ''}`}
          style={isSelected ? { borderLeftColor: meta.color } : {}}
          onClick={() => onSelect(ev.id)}
        >
          <div
            className={styles.catIcon}
            style={{ background: `${meta.color}26`, border: `1px solid ${meta.color}66` }}
          >
            <span className={styles.catAbbr} style={{ color: meta.color }}>{categoryAbbr(meta, ev)}</span>
          </div>
          <div className={styles.rowInfo}>
            <div className={styles.rowTitle} title={prettyEventTitle(ev)}>{prettyEventTitle(ev)}</div>
            {ev.data_quality && ev.data_quality !== 'measured' && (
              <DataQualityBadge quality={ev.data_quality} />
            )}
            <div className={styles.rowMeta} title={[platform, ev.region].filter(Boolean).join(' · ')}>{[platform, ev.region].filter(Boolean).join(' · ')}</div>
            <div className={styles.rowBottom}>
              <SeverityBadge severity={ev.severity} size="sm" />
              <StaleBadge lastUpdated={ev.updated_at} category={staleCategoryForEv(ev)} />
              {badge && (
                <span className={styles.rowBadge} style={{ color: badge.color, borderColor: `${badge.color}55` }}>
                  {badge.label}
                </span>
              )}
            </div>
            <div className={styles.mediaBadges}>
              {mediaBadges.map((mediaBadge) => (
                <span
                  key={mediaBadge.key}
                  className={mediaBadge.tone === 'visual' ? styles.mediaBadgeTot : mediaBadge.tone === 'empty' ? styles.mediaBadgeEmpty : styles.mediaBadge}
                  title={mediaBadge.title}
                >
                  {mediaBadge.label}
                </span>
              ))}
              {localItems.length > 0 && <span className={styles.mediaBadgeTot} title="Impatti locali mappati">LOC {localItems.length}</span>}
            </div>
          </div>
        </div>
        {localItems.length > 0 && (
          <div className={styles.subeventNest}>
            {localItems.map((sub) => (
              <div
                key={sub.id}
                className={`${styles.subeventItem} ${selectedSubevent?.id === sub.id ? styles.subeventItemSel : ''}`}
                onClick={() => onSelectSubevent && onSelectSubevent(sub)}
              >
                <div className={styles.subeventHead}>
                  <span className={styles.subeventKind}>{sub.subcategory || 'Impatto locale'}</span>
                  <span className={styles.subeventPlace}>{sub.place_name || sub.region || 'Area evento'}</span>
                </div>
                <div className={styles.subeventTitle} title={sub.title}>{sub.title}</div>
                <div className={styles.subeventMeta}>
                  {Number.isFinite(Number(sub.lat)) && Number.isFinite(Number(sub.lon)) ? `${Number(sub.lat).toFixed(3)}, ${Number(sub.lon).toFixed(3)}` : 'Coordinate non disponibili'}
                </div>
                <div className={styles.subeventActions}>
                  {sub.news_url && (
                    <button className={styles.subeventBtn} onClick={(e) => { e.stopPropagation(); window.open(sub.news_url, '_blank', 'noopener,noreferrer') }}>
                      Notizia
                    </button>
                  )}
                  {sub.video_url && (
                    <button className={styles.subeventBtn} onClick={(e) => { e.stopPropagation(); window.open(sub.video_url, '_blank', 'noopener,noreferrer') }}>
                      Video
                    </button>
                  )}
                  <button className={styles.subeventBtn} onClick={(e) => { e.stopPropagation(); onSelectSubevent && onSelectSubevent(sub) }}>
                    Apri
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  function renderGroupedChild(ev) {
    const isSelected = selectedId === ev.id
    return (
      <div key={`child-${ev.id}`} className={`${styles.groupChild} ${isSelected ? styles.groupChildSel : ''}`}>
        <div className={styles.groupChildInfo}>
          <div className={styles.groupChildRegion}>{ev.region || ev.area || ev.location || prettyEventTitle(ev)}</div>
          <div className={styles.groupChildMeta}>
            <StaleBadge lastUpdated={ev.updated_at} category={staleCategoryForEv(ev)} />
          </div>
        </div>
        <button className={styles.groupChildBtn} onClick={() => onSelect(ev.id)}>
          Dettaglio
        </button>
      </div>
    )
  }

  function renderGroupCard(entry) {
    const sample = entry.members[0]
    const severity = groupSeverity(entry.members)
    const oldest = [...entry.members].sort((a, b) => getEventTimestamp(a) - getEventTimestamp(b))[0]
    const sourceLabel = prettyPlatformLabel(sample?.primary_platform || sample?.source_name || sample?.source || sample?.type || '')
    const overflowCount = Math.max(0, entry.members.length - GROUP_SUMMARY_LIMIT)
    const visibleChildren = entry.expanded ? entry.members : []

    return (
      <div key={`group-${entry.key}`} className={styles.groupBlock}>
        <button
          className={styles.groupCard}
          onClick={() => toggleGroup(entry.key, entry.storageKey)}
          title={`${entry.members.length} eventi nello stesso gruppo`}
        >
          <div className={styles.groupCardHead}>
            <SeverityBadge severity={severity} size="sm" showLabel={false} />
            <div className={styles.groupCardText}>
              <div className={styles.groupCardTitle}>
                {groupTitle(entry.members)}
                {overflowCount > 0 ? ` · e altri ${overflowCount}` : ''}
              </div>
              <div className={styles.groupCardSub}>
                <span>{sourceLabel}</span>
                <span className={styles.groupCardDot}>·</span>
                <StaleBadge lastUpdated={oldest?.updated_at} category={staleCategoryForEv(oldest)} />
              </div>
            </div>
            <span className={styles.groupChevron} aria-hidden="true">{entry.expanded ? '▾' : '▸'}</span>
          </div>
        </button>
        {visibleChildren.length > 0 && (
          <div className={styles.groupChildren}>
            {visibleChildren.map(renderGroupedChild)}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className={styles.panel}>
      {clusterIds?.length > 1 && (
        <div className={styles.clusterBanner}>📍 {clusterIds.length} eventi sovrapposti</div>
      )}
      <div className={styles.header}>
        <span className={styles.headerLabel}>EVENTI ATTIVI</span>
        <span className={styles.headerCount}>
          {activeGroupCount > 0 ? `${filteredEvents.length} eventi (${activeGroupCount} gruppi)` : `${filteredEvents.length}/${events.length}`}
        </span>
      </div>
      <TimelineFilter
        activeRange={timeline.activeRange}
        selectedPreset={timeline.preset}
        onSelectPreset={timeline.selectPreset}
        customStart={timeline.customStart}
        customEnd={timeline.customEnd}
        onSetCustomRange={timeline.setCustomRange}
        isCustom={timeline.isCustom}
        onToggleCustom={() => timeline.isCustom ? timeline.disableCustomMode() : timeline.enableCustomMode()}
      />
      <div className={styles.chipTrack}>
        {CATEGORY_CHIPS.map(chip => {
          const meta = CATEGORY_META[chip.key]
          const color = meta?.color || chip.color || '#8b949e'
          const icon = meta?.icon || chip.icon
          const active = catFilter === chip.key
          return (
            <button
              key={chip.key}
              className={`${styles.chip} ${active ? styles.chipOn : ''}`}
              style={active ? { borderColor: color, color, background: `${color}26` } : {}}
              onClick={() => setCatFilter(chip.key)}
            >
              {icon && <span>{icon}</span>}
              {chip.label}
            </button>
          )
        })}
      </div>
      <div className={styles.filterRow}>
        <select className={styles.select} value={countryFilter} onChange={e => setCountryFilter(e.target.value)}>
          <option value="all">Tutti i paesi</option>
          {countries.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className={styles.filterRow}>
        <input
          className={styles.search}
          placeholder="Cerca evento..."
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
      </div>
      <div className={styles.filterRow2}>
        <label className={styles.checkRow}>
          <input type="checkbox" checked={visualOnly} onChange={e => setVisualOnly(e.target.checked)} />
          <span>Solo con media visuali</span>
        </label>
        <div className={styles.filterTools}>
          <select className={styles.selectMini} value={activeSeverityFilter} onChange={e => setSeverity(e.target.value)}>
            <option value="all">Severita: Tutte</option>
            <option value="critical">Severita: Critico</option>
            <option value="warning">Severita: Warning</option>
            <option value="info">Severita: Info</option>
            <option value="unknown">Severita: Sconosciuta</option>
            <option value="resolved">Severita: Risolta</option>
          </select>
          <select className={styles.selectMini} value={sortMode} onChange={e => setSortMode(e.target.value)}>
            <option value="media">Ordina: Media</option>
            <option value="severity">Ordina: Severita</option>
          </select>
        </div>
      </div>
      <div className={styles.list}>
        {groupedEntries.map((entry) => {
          if (entry.kind === 'group') return renderGroupCard(entry)
          return renderEventRow(entry.ev)
        })}
        {!filteredEvents.length && (
          <div className={styles.empty}>
            {status === 'connecting' && !events.length
              ? 'Caricamento eventi live...'
              : baseEvents.length > 0 && !filteredEvents.length
              ? `Nessun evento nel range ${timeline.activeRange.label} — prova ad ampliare il range temporale`
              : 'Nessun evento con questi filtri'}
          </div>
        )}
      </div>
    </div>
  )
}
