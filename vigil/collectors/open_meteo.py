import logging
import time
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy.orm import Session

from vigil.core.models import Event

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Open-Meteo Weather"
COLLECTOR_INTERVAL = 30
COLLECTOR_ENABLED = True

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Enrich only events updated within last 14 days
ACTIVE_DAYS = 14


def _fetch_weather(lat: float, lon: float) -> dict | None:
    """Fetcha meteo corrente da Open-Meteo (gratuito, no API key)."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "current": "temperature_2m,wind_speed_10m,surface_pressure,precipitation",
        "wind_speed_unit": "kmh",
        "timezone": "UTC",
        "forecast_days": 1,
    }
    try:
        resp = httpx.get(OPEN_METEO_URL, params=params, timeout=8)
        resp.raise_for_status()
        current = resp.json().get("current", {})
        return {
            "temp_c": current.get("temperature_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "pressure_hpa": current.get("surface_pressure"),
            "precipitation_mm": current.get("precipitation"),
        }
    except Exception as e:
        logger.debug(f"Open-Meteo errore per ({lat},{lon}): {e}")
        return None


def fetch_open_meteo_weather(db: Session) -> int:
    """
    Entry point del collector Open-Meteo.
    Arricchisce gli eventi attivi con meteo live (temp, vento, pressione, precipitazioni).
    """
    logger.info("Open-Meteo: avvio arricchimento meteo")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=ACTIVE_DAYS)
    events = (
        db.query(Event)
        .filter(Event.lat.isnot(None), Event.lon.isnot(None))
        .filter(Event.updated_at >= cutoff)
        .all()
    )

    logger.info(f"Open-Meteo: {len(events)} eventi da arricchire")
    count = 0

    for event in events:
        weather = _fetch_weather(event.lat, event.lon)
        if not weather:
            continue

        # Aggiorna temperatura e precipitazioni sempre (dati meteo correnti)
        if weather.get("temp_c") is not None:
            event.temp_c = round(weather["temp_c"], 1)
        if weather.get("precipitation_mm") is not None:
            event.precipitation_mm = round(weather["precipitation_mm"], 1)

        # Aggiorna vento e pressione solo se non già forniti da fonte ufficiale
        if event.wind_kmh is None and weather.get("wind_kmh") is not None:
            event.wind_kmh = int(weather["wind_kmh"])
        if event.pressure_hpa is None and weather.get("pressure_hpa") is not None:
            event.pressure_hpa = int(weather["pressure_hpa"])

        count += 1
        # Piccola pausa per non stressare l'API (fair use)
        time.sleep(0.15)

    logger.info(f"Open-Meteo: {count} eventi arricchiti")
    return count
