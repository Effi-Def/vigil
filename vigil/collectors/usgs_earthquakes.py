"""
Collector USGS Earthquakes.
Fonte: USGS Earthquake Hazards Program — feed GeoJSON pubblico, nessuna API key.
Fetcha terremoti M4.5+ negli ultimi 30 giorni e li inserisce come Event nel DB.
"""
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.models import Event, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "USGS Earthquakes"
COLLECTOR_INTERVAL = 12
COLLECTOR_ENABLED = True

# M4.5+ ultimi 30 giorni — feed aggiornato ogni minuto da USGS
USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.geojson"

STATUS_MAP = {"red": "CRITICO", "orange": "ATTENZIONE", "blue": "MODERATO"}


def _magnitude_to_severity(mag: float) -> str:
    if mag >= 7.0:
        return "red"
    if mag >= 5.5:
        return "orange"
    return "blue"


def _parse_features(data: dict) -> list[dict]:
    events = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})

        usgs_id = feat.get("id")
        if not usgs_id:
            continue

        mag = props.get("mag")
        if mag is None:
            continue
        try:
            mag = float(mag)
        except (TypeError, ValueError):
            continue

        coords = geom.get("coordinates", [])
        lon = float(coords[0]) if len(coords) > 0 else None
        lat = float(coords[1]) if len(coords) > 1 else None

        ts_ms = props.get("time")
        started_at = None
        if ts_ms:
            try:
                started_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
            except Exception:
                pass

        place = props.get("place") or "Sconosciuto"
        title = f"Terremoto M{mag:.1f} — {place}"
        severity = _magnitude_to_severity(mag)
        url = props.get("url") or "https://earthquake.usgs.gov"

        events.append({
            "id": f"usgs-eq-{usgs_id.lower()}",
            "title": title,
            "type": "earthquake",
            "category": EventCategory.earthquake.value,
            "is_alert": False,
            "severity": severity,
            "status": STATUS_MAP[severity],
            "lat": lat,
            "lon": lon,
            "region": place,
            "wind_kmh": None,
            "pressure_hpa": None,
            "started_at": started_at,
            "_url": url,
            "_mag": mag,
        })
    return events


def _upsert_event(db: Session, data: dict) -> None:
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    event = db.query(Event).filter(Event.id == payload["id"]).first()
    if event is None:
        event = Event(**payload)
        db.add(event)
        logger.info(f"Nuovo terremoto: {payload['id']} — {payload['title']}")
    else:
        for k, v in payload.items():
            if k != "id" and v is not None:
                setattr(event, k, v)
        event.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _upsert_source(db: Session, event_id: str, url: str) -> None:
    src_id = f"usgs-src-{event_id}"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        db.add(Source(
            id=src_id,
            name="USGS Earthquakes",
            type="ufficiale",
            platform="usgs",
            url=url,
            event_id=event_id,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=0,
        ))
    else:
        src.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)


def fetch_usgs_earthquakes(db: Session) -> int:
    """Fetcha il feed GeoJSON USGS e sincronizza i terremoti nel DB."""
    logger.info("USGS Earthquakes: avvio fetch")
    try:
        resp = httpx.get(USGS_URL, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"USGS fetch fallito: {e}")
        return 0

    features = _parse_features(data)
    logger.info(f"USGS: {len(features)} terremoti nel feed")

    count = 0
    for feat in features:
        url = feat.pop("_url", "https://earthquake.usgs.gov")
        feat.pop("_mag", None)
        try:
            _upsert_event(db, feat)
            _upsert_source(db, feat["id"], url)
            count += 1
        except Exception as e:
            logger.warning(f"Errore su {feat.get('id')}: {e}")

    logger.info(f"USGS Earthquakes: {count} terremoti processati")
    return count
