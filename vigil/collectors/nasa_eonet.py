import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.models import Event, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "NASA EONET"
COLLECTOR_INTERVAL = 15
COLLECTOR_ENABLED = True

EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=100&days=30"

CATEGORY_TO_TYPE = {
    "Severe Storms": "storm",
    "Wildfires": "wildfire",
    "Volcanoes": "volcano",
    "Floods": "flood",
    "Drought": "drought",
    "Earthquakes": "earthquake",
    "Sea and Lake Ice": "ice",
    "Landslides": "landslide",
    "Snow": "snow",
    "Dust and Haze": "dust",
    "Manmade": "manmade",
    "Temperature Extremes": "heatwave",
    "Water Color": "watercolor",
}

EONET_TO_CATEGORY = {
    "Severe Storms": EventCategory.storm.value,
    "Wildfires": EventCategory.wildfire.value,
    "Volcanoes": EventCategory.volcano.value,
    "Floods": EventCategory.flood.value,
    "Drought": EventCategory.drought.value,
    "Earthquakes": EventCategory.earthquake.value,
    "Landslides": EventCategory.landslide.value,
    "Snow": EventCategory.snow.value,
    "Temperature Extremes": EventCategory.extreme_heat.value,
}

SEVERITY_MAP = {
    "storm": "orange",
    "wildfire": "orange",
    "volcano": "red",
    "flood": "orange",
    "earthquake": "orange",
    "heatwave": "orange",
    "landslide": "orange",
    "drought": "blue",
    "ice": "blue",
    "snow": "blue",
    "dust": "blue",
    "watercolor": "blue",
    "manmade": "orange",
}

STATUS_MAP = {
    "red": "CRITICO",
    "orange": "ATTENZIONE",
    "blue": "MODERATO",
}


def _extract_coords(geometry: dict) -> tuple[Optional[float], Optional[float]]:
    coords = geometry.get("coordinates", [])
    gtype = geometry.get("type", "")
    if not coords:
        return None, None
    if gtype == "Point":
        return float(coords[1]), float(coords[0])
    if gtype == "Polygon" and coords:
        ring = coords[0]
        if ring:
            lon = sum(c[0] for c in ring) / len(ring)
            lat = sum(c[1] for c in ring) / len(ring)
            return float(lat), float(lon)
    return None, None


def _parse_eonet(data: dict) -> list[dict]:
    results = []
    for ev in data.get("events", []):
        eonet_id = ev.get("id")
        if not eonet_id:
            continue

        categories = ev.get("categories", [])
        category_title = categories[0]["title"] if categories else "Unknown"
        event_type = CATEGORY_TO_TYPE.get(category_title, category_title.lower().replace(" ", "_"))
        event_category = EONET_TO_CATEGORY.get(category_title)
        severity = SEVERITY_MAP.get(event_type, "blue")

        geometries = ev.get("geometry", [])
        lat, lon = None, None
        if geometries:
            lat, lon = _extract_coords(geometries[-1])

        started_at = None
        if geometries:
            date_str = geometries[0].get("date")
            if date_str:
                try:
                    started_at = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    pass

        title = ev.get("title", f"{event_type} event")
        sources_raw = ev.get("sources", [])

        results.append({
            "id": f"eonet-{eonet_id.lower()}",
            "title": title,
            "type": event_type,
            "category": event_category,
            "is_alert": False,
            "severity": severity,
            "status": STATUS_MAP.get(severity, "MODERATO"),
            "lat": lat,
            "lon": lon,
            "region": None,
            "wind_kmh": None,
            "pressure_hpa": None,
            "started_at": started_at,
            "_sources": sources_raw,
        })

    return results


def _upsert_event(db: Session, data: dict) -> None:
    event = db.query(Event).filter(Event.id == data["id"]).first()
    if event is None:
        event = Event(**data)
        db.add(event)
        logger.info(f"Nuovo evento EONET: {data['id']} — {data['title']}")
    else:
        for k, v in data.items():
            if k != "id" and v is not None:
                setattr(event, k, v)
        event.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)


def _upsert_source(db: Session, event_id: str) -> None:
    src_id = f"eonet-src-{event_id}"
    existing = db.query(Source).filter(Source.id == src_id).first()
    if existing is None:
        src = Source(
            id=src_id,
            name="NASA EONET",
            type="ufficiale",
            platform="eonet",
            url="https://eonet.gsfc.nasa.gov",
            event_id=event_id,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=0,
        )
        db.add(src)
    else:
        existing.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)


def fetch_nasa_eonet_events(db: Session) -> int:
    """
    Entry point del collector NASA EONET.
    Fetcha gli eventi naturali aperti e li inserisce nel DB.
    """
    logger.info("NASA EONET collector: avvio fetch")

    try:
        response = httpx.get(EONET_URL, timeout=20)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        logger.error(f"NASA EONET fetch fallito: {e}")
        return 0

    events_data = _parse_eonet(data)
    logger.info(f"NASA EONET: {len(events_data)} eventi trovati")

    count = 0
    for event_data in events_data:
        sources_raw = event_data.pop("_sources", [])
        try:
            _upsert_event(db, event_data)
            _upsert_source(db, event_data["id"])
            count += 1
        except Exception as e:
            logger.warning(f"Errore su evento EONET {event_data.get('id')}: {e}")
            continue

    logger.info(f"NASA EONET collector: {count} eventi processati")
    return count
