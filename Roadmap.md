# Vigil — Roadmap

> Piattaforma personale di monitoraggio eventi estremi, climatologia e meteorologia.
> Obiettivo finale: war room interattiva per l'esplorazione di eventi naturali, con profondità informativa massima, copertura temporale storica e layer territoriali sincronizzati.

Ultimo aggiornamento: 2026-04-10

---

## Stato attuale (v0.2)

- [x] Backend FastAPI + APScheduler + SQLite
- [x] Collector multi-sorgente: USGS, GDACS, NASA EONET, DPC, Meteoalarm, NOAA NWS/NHC, Open-Meteo, ReliefWeb, Reddit, RSS, Telegram, Wikimedia
- [x] Matching eventi/media (euristico + semantico)
- [x] Frontend React/Vite con mappa Leaflet
- [x] WebSocket eventi real-time
- [x] Marker differenziati per categoria evento
- [x] Pannello dettaglio con tab Dati / Notizie / Media / Storico / AI
- [x] Deploy base Linux con `deploy.sh` + `vigil.service` + `nginx.conf`

---

## v0.2.1 — Deploy & hardening

Obiettivo: rendere il progetto pubblicabile su VPS con un flusso semplice e ripetibile.

- [x] Bootstrap deploy Ubuntu/Debian con script unico
- [x] Build frontend automatica in fase di deploy
- [x] Pubblicazione static frontend in `/var/www/vigil`
- [x] Reverse proxy Nginx per `/api` e WebSocket
- [x] Servizio `systemd` single-worker per evitare scheduler duplicati
- [x] Guida operativa `DEPLOYMENT.md`
- [x] HTTPS con Let's Encrypt (`certbot`) e dominio custom
- [x] Backup automatico di `vigil.db`
- [x] Logging e rotating file per produzione

---

## In corso (v0.3 — UI War Room)

Obiettivo: trasformare il frontend in una vera war room operativa, stile centro operativo FBI/NOAA.

- [x] Layout 3 colonne fisso: lista eventi | mappa | dettaglio
- [x] Filtro categoria eventi (chip colorati)
- [x] Filtro paese (normalizzato da location string)
- [x] Filtro ricerca testo
- [x] Pannello dettaglio adattivo per categoria:
  - `earthquake` → parametri sismici + sismicità area + meteo collassato
  - `storm/cyclone/wind` → parametri meteo prominenti + allerta + storico
  - `wildfire` → FRP + condizioni favorevoli + fire risk indicator
  - `flood` → precipitazioni recenti sparkline + meteo area
  - `volcano` → alert level + sismicità associata + direzione vento
- [x] Layer bar mappa: Radar / Sat IR / Vento / Precipitazioni (toggle)
- [x] RainViewer radar overlay su Leaflet
- [x] Popup mappa minimal con "Apri scheda"
- [x] Endpoint `/events/{id}/climate-context` on-demand (Open-Meteo + Meteostat)
- [x] Tab Storico: condizioni attuali + media storica 10 anni + anomalie + stazione più vicina
- [x] Sezione confronto storico con barre anomalia

---

## v1.0 — Profondità informativa per evento

Obiettivo: per ogni evento, una "scheda investigativa" completa. Tutto il possibile su quell'evento specifico.

### Nuove fonti da integrare
- [ ] **NASA FIRMS** — incendi attivi da satellite (VIIRS NOAA-20), collector 20 min
- [ ] **EMSC** — sismicità europea + felt reports (persone che segnalano di aver sentito il terremoto)
- [ ] **VAAC (Volcanic Ash Advisory Centers)** — dispersione cenere vulcanica
- [ ] **Wikipedia API** — per eventi grandi viene creata una pagina entro ore, recuperarla automaticamente
- [ ] **YouTube Data API** — ricerca video per titolo evento (gratuita fino a quota giornaliera)
- [ ] **Copernicus EMS activation maps** — mappe satellitari post-evento per alluvioni e incendi

### Miglioramenti scheda evento
- [ ] Endpoint `/events/search?near_lat&near_lon&radius_km` — geo search per sismicità area
- [ ] Timeline aggiornamenti arricchita con tutte le sorgenti
- [ ] Confidence score visibile su ogni notizia/media
- [ ] Link diretto a fonte ufficiale per ogni evento
- [ ] Sezione "eventi correlati" (stesso tipo, stessa area, ultimi 30 giorni)

---

## v2.0 — Layer idrometrico

Obiettivo: monitoraggio in real-time dei livelli idrometrici, con focus su Emilia-Romagna e Ravenna.

### Fonti
- [ ] **ARPA Emilia-Romagna** — idrometri real-time, aggiornamento 30 min, rete completa E-R
  - Canali di bonifica Ravenna (Lamone, Montone, Ronco, Savio, Reno)
  - API open data disponibile
- [ ] **Protezione Civile Nazionale** — rete idrometrica nazionale
- [ ] **SIMPO (Servizio IdroMeteoPluviometrico E-R)** — pluviometri + idrometri

### Feature
- [ ] Collector idrometrico ARPA E-R (intervallo 30 min)
- [ ] Nuovo modello DB `hydrometric_readings` (stazione, livello, timestamp, soglia_1/2/3)
- [ ] Layer mappa idrometri: punti colorati per livello rispetto alle soglie ufficiali
  - Verde: sotto soglia 1
  - Giallo: soglia 1 (attenzione)
  - Arancio: soglia 2 (preallarme)
  - Rosso: soglia 3 (allarme)
- [ ] Grafico livello ultime 24-72h nella scheda evento alluvione
- [ ] Soglie ufficiali tracciate sul grafico
- [ ] Notifica (in-app) quando sensore Ravenna supera soglia

---

## v3.0 — Timeline temporale sincronizzata

Obiettivo: cursore temporale globale che sincronizza tutti i layer, come Windy ma con eventi + media + idrometri sovrapposti.

### Componente timeline player
- [ ] Barra temporale in basso alla mappa (non sovrapposta)
- [ ] Cursore trascinabile con range selezionabile: 6h / 24h / 7gg / 30gg
- [ ] Pulsanti: play / pausa / step -1h / step +1h
- [ ] Indicatore "LIVE" quando cursore è al presente
- [ ] Velocità playback regolabile (1x / 2x / 5x)

### Layer sincronizzati dal cursore
- [ ] **Radar** — RainViewer frames storici (copertura ~2h passato, poi archivio)
- [ ] **Vento** — Open-Meteo Historical orario
- [ ] **Temperatura** — Open-Meteo Historical orario
- [ ] **Precipitazioni** — Open-Meteo Historical orario
- [ ] **Idrometri** — ARPA E-R archivio orario
- [ ] **Eventi mappa** — mostra solo eventi attivi al timestamp selezionato
- [ ] **Media/foto** — pannello filtra notizie/immagini per timestamp ±2h

### Note tecniche
- Ogni layer ha frequenza e buchi diversi — gestione graceful dei dati mancanti (mostra ultimo disponibile + indicatore "dati non disponibili per questo orario")
- Cache locale dei frame radar già scaricati per evitare re-fetch durante playback
- Sincronizzazione via stato globale (Zustand o Context) — tutti i layer leggono `currentTimestamp`

---

## v4.0 — AI comparativa e analisi territorio

Obiettivo: analisi automatica degli eventi, confronto con storico, anomalie climatiche, visione d'insieme del territorio italiano.

### AI per evento
- [ ] Tab AI nella scheda evento: sommario automatico generato da Claude API
- [ ] "Quanto è anomalo questo evento?" — confronto statistico con distribuzione storica dell'area
- [ ] "Eventi simili in passato" — retrieval semantico su DB storico
- [ ] Stima impatto potenziale basata su dati storici analoghi

### Layer territoriali Italia
- [ ] Mappa choropleth regioni italiane per:
  - Anomalia termica mensile (vs media 1991-2020)
  - Anomalia precipitazioni mensile
  - Indice di siccità SPI (Standard Precipitation Index)
  - Numero eventi estremi YTD vs media storica
- [ ] Dati da: Copernicus C3S / ERA5, ISPRA CLIMADAT, ECA&D E-OBS
- [ ] Aggiornamento mensile (non real-time — dati climatici hanno latenza naturale)

### Fonti aggiuntive per Italia
- [ ] **ECA&D / E-OBS** — dati osservativi europei ad alta risoluzione, precipitazioni/temperature
- [ ] **ISPRA CLIMADAT** — indicatori climatici italiani, serie storiche anomalie
- [ ] **ARPAE E-R open data** — dataset meteo-climatici dal 1991, orari/giornalieri
- [ ] **C3S Climate Data Store / ERA5** — rianalisi gold standard, dal 1940

---

## Idee future (backlog non prioritizzato)

- Integrazione **Zoom.earth** / **Windy** come embed per confronto visivo
- Scraping foto/video da social per eventi in corso (con rate limit e disclaimer)
- Export PDF "scheda evento" per eventi notevoli
- Modalità "focus Ravenna" — dashboard dedicata con tutti i sensori locali
- Alert push (notifica browser/mobile) per eventi in Italia sopra soglia
- Confronto multi-evento: seleziona 2-3 eventi storici simili e confronta parametri

---

## Note architetturali

- **DB**: aggiungere colonne solo quando necessario, preferire fetch on-demand per dati storici
- **Rate limits da monitorare**: NASA FIRMS (5000 tx/10min), YouTube Data API (quota giornaliera), Nominatim geocoder (1 req/sec)
- **Priorità cache**: dati climatici storici (TTL 3h), radar frames (TTL 10min), idrometri (TTL 30min)
- **Scalabilità**: tutto progettato per uso personale — nessun requisito di multi-utenza