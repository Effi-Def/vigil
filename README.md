<a name="header"></a>
# VIGIL

Open source disaster and extreme weather monitoring platform for Italy — war-room UI, real-time data, operational awareness.

[![CI](https://github.com/Utente/vigil/actions/workflows/ci.yml/badge.svg)](https://github.com/Utente/vigil/actions/workflows/ci.yml) ![License: MIT](https://img.shields.io/badge/License-MIT-green.svg) ![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg) ![React 18](https://img.shields.io/badge/React-18-61DAFB.svg)

![INGV](https://img.shields.io/badge/Data%20source-INGV-0A6EBD.svg) ![ARPA](https://img.shields.io/badge/Data%20source-ARPA-2D8A34.svg) ![Open-Meteo](https://img.shields.io/badge/Data%20source-Open--Meteo-0077B6.svg) ![Meteoalarm](https://img.shields.io/badge/Data%20source-Meteoalarm-E07A00.svg)

[TODO: add full-screen war-room UI screenshot at docs/screenshots/war-room-ui-full.png]

<a name="what-it-does"></a>
## What it does

VIGIL monitors earthquakes, weather alerts, hydrometric signals, wildfire-related feeds, and news/media streams connected to high-impact events. It presents this information in a Leaflet map, an event sidebar, and a focused detail view for each event. The platform is useful for researchers, extreme weather communities, volunteer civil protection groups, and civic-tech developers who need one operational picture across heterogeneous sources. VIGIL is not an official public warning system and does not replace institutional channels from DPC, INGV, or other authorities.

<a name="live-demo"></a>
## Live demo

- Demo URL: [TODO: add public Railway or Render URL, for example https://vigil-demo.onrender.com]
- Main flow capture (critical event -> detail panel -> centered map): [TODO: add animated GIF at docs/screenshots/vigil-main-flow.gif]

<a name="features"></a>
## Features

| Feature | Status |
|---|---|
| INGV real-time seismic monitoring | ✅ Stable |
| Meteoalarm weather alerts (EU) | ✅ Stable |
| Open-Meteo weather layers (radar, wind, hydro) | ✅ Stable |
| Wildfire RSS scanner | ✅ Stable |
| ARPA hydrometric data | ⚠️ Synthetic |
| AI event summary (FastAPI + LLM) | ✅ Stable |
| Multi-source confidence score | 🔲 Roadmap v2 |
| Event audit trail | 🔲 Roadmap v2 |
| Mobile / tablet UI | 🔲 Roadmap v2 |
| Postgres database (production) | 🔲 Roadmap v2 |

⚠️ Hydrometric data is currently synthetic - the `data_quality` field is explicitly set on every record. Do not use this data for real operational decisions until live feed integration is completed.

<a name="architecture"></a>
## Architecture

```text
External sources -> Collectors (Python) -> Scheduler
                     |
                     v
               FastAPI routes -> SQLite DB
                     |
                     v
        React/Vite UI (Leaflet + war-room layout)
```

- External sources: institutional feeds, weather APIs, and media/RSS endpoints.
- Collectors + Scheduler: periodic ingestion jobs normalize and timestamp incoming records.
- FastAPI + SQLite: API routes expose event, media, geo, and health data for UI and tools.
- React/Vite + Leaflet: war-room interface renders map layers, timeline context, and event detail.

<a name="getting-started"></a>
## Getting started

### Quick start (Docker)

```bash
git clone https://github.com/USERNAME/vigil
cd vigil
cp .env.example .env
docker compose up
```

Open http://localhost:5173

[TODO: add docker-compose.yml at repository root to make this path executable as-is]

### Manual setup

```bash
# Backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py

# Frontend (new terminal)
cd vigil-frontend
npm install && npm run dev
```

<a name="environment-variables"></a>
## Environment variables

| Variable | Default | Description | Required |
|---|---|---|---|
| CORS_ORIGINS | http://localhost:5173 | Allowed CORS origins | Yes |
| DATABASE_URL | sqlite:///vigil.db | Database connection string | Yes |
| OPENAI_API_KEY | — | Key used for AI event summary | No |
| SCHEDULER_WORKERS | 2 | Scheduler worker thread pool size | No |
| LOG_LEVEL | INFO | Application log level | No |

[TODO: map these generic names to runtime variables currently used by the backend (for example VIGIL_DB_URL and VIGIL_ALLOWED_ORIGINS) or align code/env naming]

<a name="data-sources"></a>
## Data sources

| Source | Type | Refresh | Coverage | Status |
|---|---|---|---|---|
| INGV | Seismic | Real-time | Italy | ✅ Live |
| Meteoalarm | Alerts | ~15 min | Europe | ✅ Live |
| Open-Meteo | Weather | ~10 min | Global | ✅ Live |
| RSS DPC/ANSA/Meteo | Media | ~30 min | Italy | ✅ Live |
| ARPA hydrometric | Hydro | ~10 min | Regional | ⚠️ Synthetic |

<a name="operational-notes"></a>
## Operational notes

- Every record should expose a data_quality field. Check it before any interpretation.
- Apply stale thresholds per category (for example seismic 2 minutes, weather 10 minutes).
- SQLite is fit for development and demos; use Postgres for continuous operations.
- Data is not auto-pruned. Define and enforce a retention policy.
- Do not use VIGIL as a single source during real emergencies.
- Cross-check critical events against official channels before escalation.

<a name="roadmap"></a>
## Roadmap

| v1 (current) | v2 (planned) |
|---|---|
| INGV seismic real-time monitoring | Multi-source confidence score |
| Meteoalarm weather alerts (EU) | Event audit trail |
| Open-Meteo weather layers | Postgres production database |
| Wildfire RSS scanner | Mobile and tablet UI |
| ARPA hydrometric synthetic data layer | Live ARPA hydrometric feed integration |
| AI event summary (FastAPI + LLM) |  |

<a name="contributing"></a>
## Contributing

Open an issue for bugs and feature requests. Pull requests are welcome; open an issue first for non-trivial changes so scope and approach are clear. Use conventional commits (`feat`, `fix`, `docs`, `chore`). New collectors must include tests with `pytest` and mocked HTTP responses.

<a name="license-and-credits"></a>
## License & credits

- MIT License
- Credits: INGV, ARPA, Open-Meteo, Meteoalarm, Leaflet, OpenStreetMap contributors
- Vigil is not affiliated with DPC, INGV, or other institutional entities. Data is provided for informational and research purposes.
