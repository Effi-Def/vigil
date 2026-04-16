export const MOCK_EVENTS = [
  { id: 'ma-ita-er-001', title: 'Meteoalarm: allerta rossa idraulica', region: 'Emilia-Romagna', type: 'meteoalarm', severity: 'red', status: 'CRITICO', lat: 44.5, lon: 11.3, wind_kmh: null, pressure_hpa: null, media_count: 2, updated_at: new Date(Date.now() - 12 * 60000).toISOString() },
  { id: 'ma-ita-ve-002', title: 'Meteoalarm: allerta gialla temporali', region: 'Veneto', type: 'meteoalarm', severity: 'blue', status: 'MODERATO', lat: 45.6, lon: 11.7, wind_kmh: null, pressure_hpa: null, media_count: 1, updated_at: new Date(Date.now() - 20 * 60000).toISOString() },
  { id: 'dpc-to-003', title: 'DPC vigilanza: criticita moderata piogge', region: 'Toscana', type: 'dpc_vigilanza', severity: 'orange', status: 'ATTENZIONE', lat: 43.7, lon: 11.2, wind_kmh: 60, pressure_hpa: null, media_count: 3, updated_at: new Date(Date.now() - 35 * 60000).toISOString() },
  { id: 'dpc-ve-004', title: 'DPC vigilanza: criticita elevata vento', region: 'Veneto', type: 'dpc_vigilanza', severity: 'red', status: 'CRITICO', lat: 45.5, lon: 12.0, wind_kmh: 95, pressure_hpa: null, media_count: 2, updated_at: new Date(Date.now() - 55 * 60000).toISOString() },
  { id: 'tc-001', title: 'Tifone Mawar', region: 'Filippine', type: 'cyclone', severity: 'red', status: 'CRITICO', lat: 13, lon: 125, wind_kmh: 195, pressure_hpa: 912, updated_at: new Date(Date.now() - 8 * 60000).toISOString() },
  { id: 'fl-002', title: 'DANA — Valencia', region: 'Spagna', type: 'flood', severity: 'red', status: 'CRITICO', lat: 39.5, lon: -0.4, wind_kmh: null, pressure_hpa: null, updated_at: new Date(Date.now() - 22 * 60000).toISOString() },
  { id: 'tc-003', title: 'Ciclone Freddy', region: 'Mozambico', type: 'cyclone', severity: 'red', status: 'CRITICO', lat: -18, lon: 37, wind_kmh: 165, pressure_hpa: 935, updated_at: new Date(Date.now() - 65 * 60000).toISOString() },
  { id: 'st-004', title: 'Supercella Oklahoma', region: 'USA Centrale', type: 'storm', severity: 'orange', status: 'ATTENZIONE', lat: 35.5, lon: -97.5, wind_kmh: 92, pressure_hpa: 987, updated_at: new Date(Date.now() - 34 * 60000).toISOString() },
  { id: 'vo-005', title: 'Eruzione Fagradalsfjall', region: 'Islanda', type: 'volcano', severity: 'orange', status: 'ATTENZIONE', lat: 63.9, lon: -22.3, wind_kmh: null, pressure_hpa: null, updated_at: new Date(Date.now() - 120 * 60000).toISOString() },
  { id: 'sn-006', title: 'Blizzard Dakotas', region: 'USA Nord', type: 'snow', severity: 'blue', status: 'MODERATO', lat: 46, lon: -100, wind_kmh: 78, pressure_hpa: 998, updated_at: new Date(Date.now() - 41 * 60000).toISOString() },
  { id: 'dr-007', title: 'Siccità estrema Maghreb', region: 'Africa Nord', type: 'drought', severity: 'orange', status: 'ATTENZIONE', lat: 34, lon: 4, wind_kmh: null, pressure_hpa: null, updated_at: new Date(Date.now() - 300 * 60000).toISOString() },
  { id: 'ic-008', title: 'Gelicidio Appennino', region: 'Italia', type: 'ice', severity: 'blue', status: 'MODERATO', lat: 44.2, lon: 11.5, wind_kmh: 45, pressure_hpa: 1009, updated_at: new Date(Date.now() - 180 * 60000).toISOString() },
]

export const MOCK_MEDIA = {
  'tc-001': [
    { id: 1, caption: 'Immagine HIMAWARI-9 occhio del tifone. Diametro stimato 65km.', author: '@typhoon_ph', platform: 'telegram', confidence: 92, icon: '🛰', label: 'satellite HIMAWARI' },
    { id: 2, caption: 'Surge costiero su Luzon. Onde stimate 6-8m sulla costa est.', author: 'u/luzon_wx', platform: 'reddit', confidence: 78, icon: '🌊', label: 'surge costiero' },
    { id: 3, caption: 'Palme piegate dal vento a Legazpi City. Velocità stimata 180+ km/h.', author: '@storm_ph', platform: 'flickr', confidence: 85, icon: '💨', label: 'vento a terra' },
    { id: 4, caption: 'Livestream da stazione meteo privata sull\'isola di Samar.', author: 'NWS Pacific', platform: 'gdacs', confidence: 95, icon: '📹', label: 'live stream' },
    { id: 5, caption: 'Loop radar NWS Asia — precipitazioni > 200mm/h nel settore nord-est.', author: '@storm_asia', platform: 'telegram', confidence: 88, icon: '📡', label: 'radar loop' },
    { id: 6, caption: 'Foto da utente locale — prime ondate di mareggiata su Luzon nord.', author: 'u/ph_watch', platform: 'reddit', confidence: 65, icon: '🏘', label: 'danni costieri' },
  ],
  'fl-002': [
    { id: 7, caption: 'Via Colón completamente allagata. Livello stimato 1.2m.', author: 'u/val_meteo', platform: 'reddit', confidence: 82, icon: '🌧', label: 'alluvione urbana' },
    { id: 8, caption: 'Immagine MSG/Eumetsat della DANA stazionaria. Cluster convettivo da 18h.', author: '@meteosat_eu', platform: 'telegram', confidence: 90, icon: '🛰', label: 'DANA satellite' },
    { id: 9, caption: 'Cella temporalesca fotografata da Alzira. Attività elettrica estrema.', author: '@storm_es', platform: 'flickr', confidence: 71, icon: '🌩', label: 'fulminazione' },
    { id: 10, caption: 'Video amatoriale: torrente che esonda in centro storico.', author: 'u/spain_wx', platform: 'reddit', confidence: 58, icon: '📱', label: 'video virale' },
  ],
  'st-004': [
    { id: 11, caption: 'Wall cloud in rotazione visibile a SW di Oklahoma City.', author: '@okc_chaser', platform: 'flickr', confidence: 90, icon: '⛈', label: 'wall cloud' },
    { id: 12, caption: 'Live dal veicolo di un chaser. Supercella a ~3km, hail 5cm segnalato.', author: 'Storm Chasers', platform: 'gdacs', confidence: 93, icon: '📹', label: 'chase live' },
    { id: 13, caption: 'Touchdown confermato da NWS. EF2 stimato. Percorso ~15km.', author: 'u/ok_storm', platform: 'reddit', confidence: 85, icon: '🌪', label: 'tornado EF2' },
    { id: 14, caption: 'Prodotto dual-pol WSR-88D. CC basso indica detrito in vortice.', author: '@us_radar', platform: 'telegram', confidence: 95, icon: '📡', label: 'dual-pol radar' },
  ],
  'vo-005': [
    { id: 15, caption: 'Flusso di lava sul fianco nord. Avanzamento ~200m/h.', author: '@iceland_v', platform: 'flickr', confidence: 88, icon: '🌋', label: 'flusso lavico' },
    { id: 16, caption: 'Timelapse apertura fessura eruttiva. 3 bocche attive.', author: 'Volcanoes YT', platform: 'gdacs', confidence: 80, icon: '📹', label: 'timelapse' },
    { id: 17, caption: 'Mappa dispersione SO₂ da Copernicus TROPOMI.', author: '@copernicus', platform: 'telegram', confidence: 91, icon: '💨', label: 'pennacchio SO₂' },
  ],
}

export const MOCK_SOURCES = {
  'tc-001': [
    { id: 's1', name: 'NHC Western Pacific', type: 'ufficiale', platform: 'gdacs', item_count: 0 },
    { id: 's2', name: 'r/typhoons', type: 'reddit', platform: 'reddit', item_count: 24 },
    { id: 's3', name: '@typhoon_ph (Telegram)', type: 'social', platform: 'telegram', item_count: 18 },
    { id: 's4', name: 'Flickr #typhoon', type: 'flickr', platform: 'flickr', item_count: 7 },
  ],
  'fl-002': [
    { id: 's5', name: 'AEMET Aviso Rojo', type: 'ufficiale', platform: 'gdacs', item_count: 0 },
    { id: 's6', name: 'r/spain', type: 'reddit', platform: 'reddit', item_count: 11 },
    { id: 's7', name: '@meteocat (Telegram)', type: 'social', platform: 'telegram', item_count: 9 },
  ],
  'st-004': [
    { id: 's8', name: 'NWS Norman OK', type: 'ufficiale', platform: 'gdacs', item_count: 0 },
    { id: 's9', name: 'Storm Prediction Center', type: 'ufficiale', platform: 'gdacs', item_count: 0 },
    { id: 's10', name: 'r/meteorology', type: 'reddit', platform: 'reddit', item_count: 19 },
    { id: 's11', name: '@us_radar (Telegram)', type: 'social', platform: 'telegram', item_count: 6 },
  ],
  'vo-005': [
    { id: 's12', name: 'IMO Veðurstofa Íslands', type: 'ufficiale', platform: 'gdacs', item_count: 0 },
    { id: 's13', name: 'Copernicus EMS', type: 'ufficiale', platform: 'gdacs', item_count: 0 },
    { id: 's14', name: 'r/volcanoes', type: 'reddit', platform: 'reddit', item_count: 8 },
  ],
}
