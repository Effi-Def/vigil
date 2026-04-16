export const SEVERITY_CONFIG = {
  critical: {
    label: 'Critico',
    color: 'var(--color-text-danger)',
    bg: 'var(--color-background-danger)',
    borderStyle: 'solid',
    icon: 'alert-octagon',
    audioEnabled: true,
  },
  warning: {
    label: 'Attenzione',
    color: 'var(--color-text-warning)',
    bg: 'var(--color-background-warning)',
    borderStyle: 'solid',
    icon: 'alert-triangle',
    audioEnabled: false,
  },
  info: {
    label: 'Info',
    color: 'var(--color-text-info)',
    bg: 'var(--color-background-info)',
    borderStyle: 'solid',
    icon: 'info',
    audioEnabled: false,
  },
  unknown: {
    label: 'Sconosciuto',
    color: 'var(--color-text-tertiary)',
    bg: 'var(--color-background-secondary)',
    borderStyle: 'dashed',
    icon: 'help-circle',
    audioEnabled: false,
  },
  resolved: {
    label: 'Risolto',
    color: 'var(--color-text-success)',
    bg: 'var(--color-background-success)',
    borderStyle: 'solid',
    icon: 'check-circle',
    audioEnabled: false,
  },
}

export function getSeverity(rawValue) {
  if (rawValue == null) return 'unknown'
  const key = String(rawValue).trim().toLowerCase()
  if (!key) return 'unknown'

  if (key === 'red') return 'critical'
  if (key === 'orange' || key === 'yellow') return 'warning'
  if (key === 'blue') return 'info'
  if (key === 'green') return 'resolved'

  if (Object.prototype.hasOwnProperty.call(SEVERITY_CONFIG, key)) return key
  return 'unknown'
}
