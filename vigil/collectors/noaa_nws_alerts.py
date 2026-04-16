"""
Collector NOAA / NWS Active Weather Alerts (USA).
Fonte: api.weather.gov — JSON pubblico, nessuna API key.
Fetcha allerte meteo attive USA (Extreme/Severe/Moderate) e le inserisce come Event.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.models import Event, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "NOAA NWS Alerts"
COLLECTOR_INTERVAL = 15
COLLECTOR_ENABLED = True

NWS_URL = (
    "https://api.weather.gov/alerts/active"
    "?status=actual&message_type=alert&severity=Extreme,Severe,Moderate"
)

HEADERS = {
    "User-Agent": "vigil-monitor/0.2 (weather monitoring; contact: vigil)",
    "Accept": "application/geo+json",
}

NWS_EVENT_TO_TYPE = {
    "Tornado Warning": "storm",
    "Tornado Watch": "storm",
    "Flash Flood Warning": "flood",
    "Flash Flood Watch": "flood",
    "Flood Warning": "flood",
    "Flood Watch": "flood",
    "Hurricane Warning": "cyclone",
    "Hurricane Watch": "cyclone",
    "Tropical Storm Warning": "storm",
    "Tropical Storm Watch": "storm",
    "Severe Thunderstorm Warning": "storm",
    "Blizzard Warning": "storm",
    "Ice Storm Warning": "extreme_cold",
    "Winter Storm Warning": "snow",
    "Extreme Cold Warning": "extreme_cold",
    "Excessive Heat Warning": "extreme_heat",
    "Heat Advisory": "extreme_heat",
    "Fire Weather Watch": "wildfire",
    "Red Flag Warning": "wildfire",
    "Dust Storm Warning": "storm",
    "Dense Fog Advisory": "storm",
    "High Wind Warning": "wind",
    "Wind Advisory": "wind",
}

TYPE_TO_CATEGORY = {
    "flood": EventCategory.flood.value,
    "cyclone": EventCategory.cyclone.value,
    "storm": EventCategory.storm.value,
    "snow": EventCategory.snow.value,
    "extreme_cold": EventCategory.extreme_cold.value,
    "extreme_heat": EventCategory.extreme_heat.value,
    "wildfire": EventCategory.wildfire.value,
    "wind": EventCategory.wind.value,
}

SEVERITY_MAP = {
    "Extreme": "red",
    "Severe": "orange",
    "Moderate": "blue",
    "Minor": "blue",
    "Unknown": "blue",
}

STATUS_MAP = {"red": "CRITICO", "orange": "ATTENZIONE", "blue": "MODERATO"}


def _centroid(geometry: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    """Calcola il centroide approssimativo di una geometria GeoJSON."""
    if not geometry:
        return None, None
    gtype = geometry.get("type", "")
    coords = geometry.get("coordinates", [])
    try:
        if gtype == "Point":
            return float(coords[1]), float(coords[0])
        if gtype == "Polygon" and coords:
            ring = coords[0]
            lons = [c[0] for c in ring]
            lats = [c[1] for c in ring]
            return sum(lats) / len(lats), sum(lons) / len(lons)
        if gtype == "MultiPolygon" and coords:
            all_coords = [c for poly in coords for ring in poly for c in ring]
            lons = [c[0] for c in all_coords]
            lats = [c[1] for c in all_coords]
            return sum(lats) / len(lats), sum(lons) / len(lons)
    except Exception:
        pass
    return None, None


def _parse_features(data: dict) -> list[dict]:
    events = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        feat_id = props.get("id") or feat.get("id")
        if not feat_id:
            continue

        nws_event = props.get("event") or "Weather Alert"
        severity_raw = props.get("severity", "Moderate")
        severity = SEVERITY_MAP.get(severity_raw, "blue")

        headline = props.get("headline") or nws_event
        area = props.get("areaDesc") or "USA"
        title = f"{nws_event} — {area[:60]}"

        event_type = NWS_EVENT_TO_TYPE.get(nws_event, "storm")
        category = TYPE_TO_CATEGORY.get(event_type)

        effective = props.get("effective") or props.get("onset")
        started_at = None
        if effective:
            try:
                started_at = datetime.fromisoformat(
                    effective.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                pass

        lat, lon = _centroid(feat.get("geometry"))

        url = props.get("id") or ""

        events.append({
            "id": f"nws-{feat_id.split('/')[-1].lower()[:60]}",
            "title": title,
            "type": event_type,
            "category": category,
            "is_alert": True,
            "severity": severity,
            "status": STATUS_MAP[severity],
            "lat": lat,
            "lon": lon,
            "region": area[:120],
            "wind_kmh": None,
            "pressure_hpa": None,
            "started_at": started_at,
            "_url": url,
        })
    return events


def _upsert_event(db: Session, data: dict) -> None:
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    event = db.query(Event).filter(Event.id == payload["id"]).first()
    if event is None:
        event = Event(**payload)
        db.add(event)
        logger.info(f"Nuova allerta NWS: {payload['id']} — {payload['title']}")
    else:
        for k, v in payload.items():
            if k != "id" and v is not None:
                setattr(event, k, v)
        event.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _upsert_source(db: Session, event_id: str, url: str) -> None:
    src_id = f"nws-src-{event_id}"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        db.add(Source(
            id=src_id,
            name="NOAA NWS Alerts",
            type="ufficiale",
            platform="noaa_nws",
            url=url or "https://api.weather.gov",
            event_id=event_id,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=0,
        ))
    else:
        src.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)


def fetch_noaa_nws_alerts(db: Session) -> int:
    """Fetcha le allerte meteo attive USA da api.weather.gov."""
    logger.info("NOAA NWS: avvio fetch allerte")
    try:
        resp = httpx.get(NWS_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"NOAA NWS fetch fallito: {e}")
        return 0

    features = _parse_features(data)
    logger.info(f"NOAA NWS: {len(features)} allerte nel feed")

    count = 0
    for feat in features:
        url = feat.pop("_url", "")
        try:
            _upsert_event(db, feat)
            _upsert_source(db, feat["id"], url)
            count += 1
        except Exception as e:
            logger.warning(f"Errore su {feat.get('id')}: {e}")

    logger.info(f"NOAA NWS: {count} allerte processate")
    return count
