import asyncio
import logging
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
METEO_ENRICH_COOLDOWN_MINUTES = 30
BATCH_SIZE = 10
RATE_LIMIT_DELAY_SECONDS = 0.1
BATCH_DELAY_SECONDS = 1
RETRY_429_DELAY_SECONDS = 5


async def _fetch_weather(client: httpx.AsyncClient, lat: float, lon: float, event_id: str) -> dict | None:
    """Fetcha meteo corrente da Open-Meteo (gratuito, no API key)."""
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "current": "temperature_2m,wind_speed_10m,surface_pressure,precipitation",
        "wind_speed_unit": "kmh",
        "timezone": "UTC",
        "forecast_days": 1,
    }
    for attempt in range(2):
        try:
            resp = await client.get(OPEN_METEO_URL, params=params, timeout=8)
            if resp.status_code == 429:
                if attempt == 0:
                    logger.warning(
                        f"Open-Meteo 429 per evento {event_id}, retry tra {RETRY_429_DELAY_SECONDS}s"
                    )
                    await asyncio.sleep(RETRY_429_DELAY_SECONDS)
                    continue
                logger.warning(f"Open-Meteo 429 persistente per evento {event_id}, skip")
                return None

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

    return None


async def _enrich_event(event: Event, client: httpx.AsyncClient, start_delay: float) -> int:
    await asyncio.sleep(start_delay)

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    cooldown = now_utc - timedelta(minutes=METEO_ENRICH_COOLDOWN_MINUTES)
    if event.last_meteo_enriched and event.last_meteo_enriched >= cooldown:
        return 0

    weather = await _fetch_weather(client, event.lat, event.lon, event.id)
    if not weather:
        return 0

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

    event.last_meteo_enriched = now_utc
    return 1


async def _enrich_events_in_batches(events: list[Event]) -> int:
    count = 0
    async with httpx.AsyncClient() as client:
        for i in range(0, len(events), BATCH_SIZE):
            batch = events[i:i + BATCH_SIZE]
            tasks = [
                _enrich_event(event, client, idx * RATE_LIMIT_DELAY_SECONDS)
                for idx, event in enumerate(batch)
            ]
            results = await asyncio.gather(*tasks)
            count += sum(results)
            if i + BATCH_SIZE < len(events):
                await asyncio.sleep(BATCH_DELAY_SECONDS)
    return count


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
    count = asyncio.run(_enrich_events_in_batches(events))

    logger.info(f"Open-Meteo: {count} eventi arricchiti")
    return count
