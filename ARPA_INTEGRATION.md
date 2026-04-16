# ARPA Hydrometric Integration Guide

## Overview

The Vigil disaster monitoring system includes ARPA (Agenzia Regionale per la Prevenzione e l'Ambiente) hydrometric and pluviometric data collection. This enhances the Territory view with hydrometric station data from regional monitoring networks across Italy.

> **Stato attuale — dati sintetici**: il collector (`vigil/collectors/arpa_hydro.py`) genera valori con pattern diurno simulato. Non è ancora attiva una connessione verso le API WMS/WFS ARPA reali. I valori esposti dalla UI recano il badge **DATO SIMULATO**. Roadmap integrazione live: vedi sezione [TODO for Production](#todo-for-production-) più avanti in questo documento.

## Architecture

### Components

1. **HydroStation Model** (`vigil/core/models.py`)
   - New database table storing hydrometric station data
   - Fields: water_level_m, discharge_m3s, precip_mm, precip_24h_mm, hydro_index, hydro_level
   - Data source tracking: 'measured' (real ARPA) or 'estimated' (local calculation)

2. **ARPA Collector** (`vigil/collectors/arpa_hydro.py`)
   - Runs on configurable interval (default: 30 minutes)
   - Fetches data from ARPA Emilia-Romagna network
   - Computes hydro level categories (normal/moderate/high) based on discharge rates
   - Stores measurements in HydroStation table with timestamps

3. **Enhanced API Endpoints** (`main.py`)
   - `/geo/stations`: Now returns both ARPA stations (measured) and event-based stations (estimated)
   - `/geo/territory-summary`: Includes ARPA hydro level counts in metrics

### Data Flow

```
ARPA Networks (Emilia-Romagna, etc)
    ↓
arpa_hydro.py collector (30-min interval)
    ↓
HydroStation table (SQLite)
    ↓ (Priority)
/geo/stations endpoint
    ↓ (Fallback)
Event table + local estimation
```

## Station Network

### Currently Supported: Emilia-Romagna Rivers

L'MVP include 8 stazioni idrometriche campione sui principali corsi d'acqua. I dati sono **sintetici** (pattern diurno simulato) fino a quando non sarà completata l'integrazione con le API ARPA reali.

| Station | River | Location | Code |
|---------|-------|----------|------|
| arpa-em-po-piacenza | Po | Piacenza | DT_RFI000 |
| arpa-em-po-rottanova | Po | Rottanova | DT_RFI001 |
| arpa-em-reno-bagnacavallo | Reno | Bagnacavallo | DT_RFI002 |
| arpa-em-secchia-reggioemilia | Secchia | Reggio Emilia | DT_RFI003 |
| arpa-em-panaro-modena | Panaro | Modena | DT_RFI004 |
| arpa-em-reno-ferrara | Reno | Ferrara | DT_RFI005 |
| arpa-em-lamone-ravenna | Lamone | Ravenna | DT_RFI006 |
| arpa-em-montone-forlì | Montone | Forlì | DT_RFI007 |

## Hydro Level Classification

Discharge rates are classified into severity levels:

- **High** (🔴 Red): discharge_m3s > 300 m³/s
- **Moderate** (🟠 Orange): discharge_m3s 150-300 m³/s
- **Normal** (🟢 Green): discharge_m3s < 150 m³/s

Thresholds are approximate and should be calibrated per river based on historical data and basin characteristics.

## API Response Schema

### Station Object (from `/geo/stations`)

```json
{
  "id": "arpa-em-po-piacenza",
  "name": "Po at Piacenza",
  "lat": 45.0507,
  "lon": 9.7049,
  "type": "hydro_station",
  "provider": "arpa-em",
  "river": "Po",
  "water_level_m": 2.845,
  "discharge_m3s": 95.4,
  "discharge_max_m3s": null,
  "precip_mm": 0.2,
  "precip_24h_mm": 1.5,
  "hydro_level": "normal",
  "hydro_color": "#3fb950",
  "hydro_index": 4.77,
  "data_source": "measured",
  "updated_at": "2026-04-01T19:10:00"
}
```

### Territory Summary (from `/geo/territory-summary`)

The metrics now include ARPA hydro levels:

```json
{
  "metrics": {
    "event_count": 8,
    "temp_avg_c": 9.8,
    "wind_avg_kmh": 20.3,
    "precip_avg_mm": 0.0,
    "hydro_levels": {
      "high": 0,
      "moderate": 0,
      "normal": 8
    }
  }
}
```

The `hydro_levels` dict now aggregates both event-based and ARPA station data.

## Current Implementation Status

### Implemented ✅

- [x] HydroStation model with full field support
- [x] ARPA collector framework with synthetic data generation
- [x] Integration with /geo/stations endpoint (priority-based merging)
- [x] Integration with /geo/territory-summary endpoint
- [x] Hydro level computation and color mapping
- [x] Data source tracking (measured vs. estimated)

### TODO for Production 📋

1. **Real ARPA API Integration**
   - Implement WMS/WFS client for actual ARPA Emilia-Romagna endpoints
   - Current code uses synthetic diurnal patterns for MVP
   - Endpoints to explore:
     - https://webgis.arpa.emilia-romagna.it/
     - https://www.parer.emilia-romagna.it/featurelist/
     - ADBPO (AdBPO - Po River Basin Authority)

2. **Other Regional ARPA Support**
   - ARPA Piemonte (Piedmont)
   - ARPA Veneto (Veneto)
   - ARPA Toscana (Tuscany)
   - etc.

3. **Threshold Calibration**
   - Collect historical discharge data per river basin
   - Validate hydro level cutoffs against real flood events
   - Implement river-specific thresholds in database

4. **Alert Integration**
   - Create alert rules based on hydro levels
   - Trigger notifications when stations reach "high" or "moderate"
   - Historical tracking of alert accuracy

5. **Forecast Data**
   - Integrate discharge forecasts (24h, 48h, 72h ahead)
   - Show in Territory panel alongside measured values

## Frontend Integration

### Territory Panel Changes

When a user switches to "Territorio" view in the Topbar and zooms into a region, the Territory Panel (`TerritoryPanel.jsx`) now displays:

- **Hydro Levels Pills**: Color-coded counts of stations by severity
  - High (red): # stations
  - Moderate (orange): # stations
  - Normal (green): # stations

- **Stations List**: Shows both ARPA measured and event-based estimated stations
  - Name and river (for hydro stations)
  - Current water level and discharge
  - Data source indicator

### Map Overlay

The Live Overlay layer continues to show:
- Color-coded CircleMarkers for each point
- Red for high risk, orange for moderate, green for normal
- Hydro color now reflects ARPA data when available

## Database Migrations

The system automatically creates the `hydro_stations` table on startup via SQLAlchemy's `init_db()` function. No manual migration required.

If you need to reset:

```bash
rm vigil.db  # Remove SQLite database
python -c "from vigil.core.database import init_db; init_db()"
```

## Configuration

### Collector Interval

Edit `vigil/collectors/arpa_hydro.py`:

```python
COLLECTOR_INTERVAL = 30  # minutes between runs
COLLECTOR_ENABLED = True  # disable if needed
```

Scheduler respects `COLLECTOR_ENABLED` flag globally.

### Hydro Level Thresholds

Edit `_compute_hydro_level_from_discharge()` in `arpa_hydro.py`:

```python
if discharge_m3s > 300:
    level = "high"
elif discharge_m3s > 150:
    level = "moderate"
else:
    level = "normal"
```

## Testing

### Manual API Tests

```bash
# Get all stations in Emilia-Romagna bbox
curl "http://127.0.0.1:8000/geo/stations?min_lat=43.7&max_lat=45.2&min_lon=9.2&max_lon=12.9&limit=50"

# Get territory summary (includes ARPA in hydro_levels count)
curl "http://127.0.0.1:8000/geo/territory-summary?min_lat=43.7&max_lat=45.2&min_lon=9.2&max_lon=12.9&zoom=8&focus_name=Emilia-Romagna"
```

### Database Query

```python
from vigil.core.database import SessionLocal
from vigil.core.models import HydroStation

db = SessionLocal()
stations = db.query(HydroStation).all()
for s in stations:
    print(f"{s.name}: {s.discharge_m3s} m³/s ({s.hydro_level})")
```

## Troubleshooting

### Stations Not Appearing

1. Check collector is enabled: `COLLECTOR_ENABLED = True` in `arpa_hydro.py`
2. Check scheduler ran: Look for "ARPA hydro: updated X stations" in logs
3. Check database has records:
   ```bash
   sqlite3 vigil.db "SELECT COUNT(*) FROM hydro_stations;"
   ```

### Wrong Hydro Levels

1. Verify discharge data is in expected range (0-500 m³/s typical)
2. Check thresholds in `_compute_hydro_level_from_discharge()`
3. Compare to real ARPA dashboards for validation

### Performance Issues

1. Cache TTL for `/geo/stations` is dynamic (no explicit timeout)
2. If too many queries, implement request caching:
   ```python
   STATIONS_CACHE_TTL = 60  # seconds
   ```

## Future Enhancements

1. **Real-time WebSocket updates** for stations during high-water events
2. **Time-series graphs** of discharge/water level over 7 days
3. **Discharge forecast visualization** on map
4. **SMS/Push alerts** when stations reach alert thresholds
5. **Mobile app integration** with push notifications
6. **Multi-region aggregation** (show worst conditions across all ARPA networks)

## References

- ARPA Emilia-Romagna: https://www.arpa.emilia-romagna.it/
- OGC WMS/WFS Standards: https://www.ogc.org/standards/wms
- Italian Hydrometric Network: https://www.rfi.com/ (RFI = Rete di Monitoraggio)
- Po River Basin Authority (AdBPO): https://www.adbpo.it/
