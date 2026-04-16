export const CATEGORY_META = {
  earthquake: { label: 'Terremoto', icon: '🌍', color: '#e74c3c', priority: 1 },
  tsunami: { label: 'Tsunami', icon: '🌊', color: '#2980b9', priority: 1 },
  volcano: { label: 'Vulcano', icon: '🌋', color: '#e67e22', priority: 2 },
  flood: { label: 'Alluvione', icon: '💧', color: '#3498db', priority: 2 },
  landslide: { label: 'Frana', icon: '⛰️', color: '#795548', priority: 2 },
  drought: { label: 'Siccità', icon: '☀️', color: '#f39c12', priority: 3 },
  cyclone: { label: 'Ciclone', icon: '🌀', color: '#9b59b6', priority: 1 },
  storm: { label: 'Temporale', icon: '⛈️', color: '#5d6d7e', priority: 2 },
  extreme_heat: { label: 'Caldo estremo', icon: '🔥', color: '#e74c3c', priority: 2 },
  extreme_cold: { label: 'Freddo estremo', icon: '❄️', color: '#85c1e9', priority: 2 },
  snow: { label: 'Neve estrema', icon: '🌨️', color: '#aed6f1', priority: 3 },
  wind: { label: 'Vento forte', icon: '💨', color: '#717d7e', priority: 3 },
  wildfire: { label: 'Incendio', icon: '🔥', color: '#e74c3c', priority: 2 },
  humanitarian: { label: 'Umanitario', icon: '🆘', color: '#e74c3c', priority: 3 },
}

export const FALLBACK_CATEGORY = {
  label: 'Altro',
  icon: '⚠️',
  color: '#95a5a6',
  priority: 5,
}
