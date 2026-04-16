import { useState } from 'react'
import styles from './TimelineFilter.module.css'
import { formatDateForDisplay } from '../hooks/useTimelineFilter'

const PRESETS = [
  { key: '1h', label: 'Ultima ora' },
  { key: '6h', label: '6 ore', highlight: true },
  { key: '24h', label: '24 ore' },
  { key: '7d', label: '7 giorni' },
  { key: 'all', label: 'Tutto' },
]

export default function TimelineFilter({
  activeRange,
  selectedPreset,
  onSelectPreset,
  customStart,
  customEnd,
  onSetCustomRange,
  isCustom,
  onToggleCustom,
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [tempStart, setTempStart] = useState(customStart.getTime())
  const [tempEnd, setTempEnd] = useState(customEnd.getTime())

  const MIN_CUSTOM_RANGE_MS = 30 * 60 * 1000
  const now = Date.now()
  const maxHistoryMs = now - new Date('2024-01-01').getTime()

  function handleStartChange(e) {
    const newVal = Number(e.target.value)
    if (newVal < tempEnd - MIN_CUSTOM_RANGE_MS && newVal > 0) {
      setTempStart(newVal)
    }
  }

  function handleEndChange(e) {
    const newVal = Number(e.target.value)
    if (newVal > tempStart + MIN_CUSTOM_RANGE_MS && newVal <= now) {
      setTempEnd(newVal)
    }
  }

  function applyCustomRange() {
    onSetCustomRange(new Date(tempStart), new Date(tempEnd))
  }

  function closeAdvanced() {
    setShowAdvanced(false)
    setTempStart(customStart.getTime())
    setTempEnd(customEnd.getTime())
  }

  return (
    <div className={styles.container}>
      <div className={styles.presetsRow}>
        {PRESETS.map(preset => (
          <button
            key={preset.key}
            className={`${styles.preset} ${
              selectedPreset === preset.key && !isCustom ? styles.active : ''
            } ${preset.highlight ? styles.highlight : ''}`}
            onClick={() => {
              onSelectPreset(preset.key)
              setShowAdvanced(false)
            }}
          >
            {preset.label}
          </button>
        ))}
        <button
          className={`${styles.preset} ${isCustom ? styles.active : ''}`}
          onClick={() => {
            onToggleCustom()
            setShowAdvanced(!showAdvanced)
          }}
        >
          📅 Personalizzato
        </button>
      </div>

      {showAdvanced && isCustom && (
        <div className={styles.advancedPanel}>
          <div className={styles.sliderSection}>
            <label className={styles.sliderLabel}>
              Dal:
              <span className={styles.dateDisplay}>{formatDateForDisplay(tempStart)}</span>
            </label>
            <input
              type="range"
              className={styles.slider}
              min={0}
              max={now}
              value={tempStart}
              onChange={handleStartChange}
            />

            <label className={styles.sliderLabel}>
              Al:
              <span className={styles.dateDisplay}>{formatDateForDisplay(tempEnd)}</span>
            </label>
            <input
              type="range"
              className={styles.slider}
              min={0}
              max={now}
              value={tempEnd}
              onChange={handleEndChange}
            />
          </div>

          <div className={styles.rangeInfo}>
            <strong>Intervallo:</strong> {formatDateForDisplay(tempStart)} - {formatDateForDisplay(tempEnd)}
            <span className={styles.duration}>
              ({Math.round((tempEnd - tempStart) / 60000)} min)
            </span>
          </div>

          <div className={styles.buttonGroup}>
            <button className={styles.btnApply} onClick={applyCustomRange}>
              ✓ Applica
            </button>
            <button className={styles.btnCancel} onClick={closeAdvanced}>
              ✕ Annulla
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
