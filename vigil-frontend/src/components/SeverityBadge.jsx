import { SEVERITY_CONFIG, getSeverity } from '../constants/severity'

function Icon({ name, size = 14 }) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': true,
  }

  switch (name) {
    case 'alert-octagon':
      return (
        <svg {...common}>
          <path d="M7.86 2h8.28L22 7.86v8.28L16.14 22H7.86L2 16.14V7.86L7.86 2z" />
          <line x1="12" y1="8" x2="12" y2="13" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      )
    case 'alert-triangle':
      return (
        <svg {...common}>
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      )
    case 'info':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="10" x2="12" y2="16" />
          <line x1="12" y1="7" x2="12.01" y2="7" />
        </svg>
      )
    case 'help-circle':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <path d="M9.1 9a3 3 0 1 1 5.8 1c-.6 1.3-1.9 1.8-2.6 2.4-.5.4-.8.9-.8 1.6" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      )
    case 'check-circle':
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="10" />
          <polyline points="9 12 11 14 15 10" />
        </svg>
      )
    default:
      return null
  }
}

export default function SeverityBadge({ severity, size = 'md', showLabel = true }) {
  const normalized = getSeverity(severity)
  const cfg = SEVERITY_CONFIG[normalized] || SEVERITY_CONFIG.unknown
  const isSm = size === 'sm'

  return (
    <span
      title={cfg.label}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        borderRadius: 4,
        borderWidth: 1,
        borderStyle: cfg.borderStyle,
        borderColor: cfg.color,
        background: cfg.bg,
        color: cfg.color,
        fontFamily: 'var(--fm)',
        fontWeight: 700,
        fontSize: isSm ? 11 : 13,
        lineHeight: 1.2,
        letterSpacing: '0.03em',
        padding: isSm ? '2px 7px' : '4px 10px',
        whiteSpace: 'nowrap',
      }}
    >
      <Icon name={cfg.icon} size={14} />
      {showLabel ? <span>{cfg.label}</span> : null}
    </span>
  )
}
