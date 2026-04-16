# Vigil - Catalogo Fonti Dati (API/RSS/Servizi)

> Le sezioni sono distinte: solo la prima corrisponde a collector auto-discovery. <!-- # fix: [11] -->

Aggiornato al 2026-03-31.

## Collector attivi (vigil/collectors/)

- DPC Protezione Civile ([vigil/collectors/dpc.py](vigil/collectors/dpc.py))
  - `https://raw.githubusercontent.com/pcm-dpc/DPC-Bollettini-Vigilanza-Meteorologica/master/files/json/today.json`
  - fallback index: `https://api.github.com/repos/pcm-dpc/DPC-Bollettini-Vigilanza-Meteorologica/contents/files?ref=master`

- GDACS Official ([vigil/collectors/gdacs.py](vigil/collectors/gdacs.py))
  - `https://www.gdacs.org/xml/rss.xml`

- Meteoalarm EU ([vigil/collectors/meteoalarm.py](vigil/collectors/meteoalarm.py))
  - `https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-italy`

- NASA EONET ([vigil/collectors/nasa_eonet.py](vigil/collectors/nasa_eonet.py))
  - `https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=100&days=30`

- NASA FIRMS ([vigil/collectors/nasa_firms.py](vigil/collectors/nasa_firms.py)) [richiede `FIRMS_MAP_KEY`]
  - base: `https://firms.modaps.eosdis.nasa.gov`
  - API CSV area: `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/world/1`

- NOAA NWS Alerts ([vigil/collectors/noaa_nws_alerts.py](vigil/collectors/noaa_nws_alerts.py))
  - `https://api.weather.gov/alerts/active`

- NOAA NHC ([vigil/collectors/noaa_nhc.py](vigil/collectors/noaa_nhc.py))
  - Atlantico: `https://www.nhc.noaa.gov/nhc_at1.xml` ... `nhc_at5.xml`
  - Pacifico Est: `https://www.nhc.noaa.gov/nhc_ep1.xml` ... `nhc_ep5.xml`
  - Pacifico Centrale: `https://www.nhc.noaa.gov/nhc_cp1.xml`, `https://www.nhc.noaa.gov/nhc_cp2.xml`

- Open-Meteo Weather ([vigil/collectors/open_meteo.py](vigil/collectors/open_meteo.py))
  - `https://api.open-meteo.com/v1/forecast`

- USGS Earthquakes ([vigil/collectors/usgs_earthquakes.py](vigil/collectors/usgs_earthquakes.py))
  - `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson`

- ReliefWeb Reports ([vigil/collectors/reliefweb.py](vigil/collectors/reliefweb.py))
  - `https://api.reliefweb.int/v1/reports`

- Reddit Disaster RSS ([vigil/collectors/reddit_rss.py](vigil/collectors/reddit_rss.py))
  - pattern: `https://www.reddit.com/r/{subreddit}/new/.rss?limit=25`

- RSS Testate Locali ([vigil/collectors/rss_local.py](vigil/collectors/rss_local.py))
  - feed regionali/nazionali (lista completa nel dict `REGIONAL_RSS`)

- Google News RSS ([vigil/collectors/news_google.py](vigil/collectors/news_google.py))
  - pattern: `https://news.google.com/rss/search?q={query}&hl=it&gl=IT&ceid=IT:it`

- Telegram Channels ([vigil/collectors/telegram.py](vigil/collectors/telegram.py)) [richiede `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`]
  - piattaforma Telegram: `https://t.me/{channel}`

- Wikimedia Commons Images ([vigil/collectors/wikimedia_images.py](vigil/collectors/wikimedia_images.py))
  - `https://commons.wikimedia.org/w/api.php`

## Endpoint backend con chiamate esterne

- News evento ([main.py](main.py) -> `/events/{event_id}/news`)
  - `https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en`

- Climate context ([vigil/core/climate_context.py](vigil/core/climate_context.py) -> `/events/{event_id}/climate-context`)
  - `https://api.open-meteo.com/v1/forecast`
  - `https://archive-api.open-meteo.com/v1/archive`
  - Meteostat (libreria Python)

- Seismicity INGV ([vigil/core/ingv_seismicity.py](vigil/core/ingv_seismicity.py) -> `/events/{event_id}/seismicity`)
  - `https://webservices.ingv.it/fdsnws/event/1/query`

## Integrazioni frontend esterne

- Mappa radar RainViewer ([vigil-frontend/src/components/Map.jsx](vigil-frontend/src/components/Map.jsx))
  - metadata: `https://api.rainviewer.com/public/weather-maps.json`
  - tiles radar da `host/path` restituiti da RainViewer
