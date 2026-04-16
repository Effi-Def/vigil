import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.models import Event, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "GDACS Official"
COLLECTOR_INTERVAL = 10
COLLECTOR_ENABLED = True

GDACS_FEED_URL = "https://www.gdacs.org/xml/rss.xml"

SEVERITY_MAP = {
    "Green": "blue",
    "Orange": "orange",
    "Red": "red",
}

TYPE_MAP = {
    "TC": "cyclone",
    "FL": "flood",
    "EQ": "earthquake",
    "VO": "volcano",
    "DR": "drought",
    "WF": "wildfire",
}

GDACS_TYPE_TO_CATEGORY = {
    "EQ": EventCategory.earthquake.value,
    "TC": EventCategory.cyclone.value,
    "FL": EventCategory.flood.value,
    "VO": EventCategory.volcano.value,
    "TS": EventCategory.tsunami.value,
}

STATUS_MAP = {
    "red": "CRITICO",
    "orange": "ATTENZIONE",
    "blue": "MODERATO",
}


def _parse_gdacs_xml(xml_text: str) -> list[dict]:
    """Parsa il feed RSS GDACS ed estrae gli eventi attivi."""
    import xml.etree.ElementTree as ET

    ns = {
        "gdacs": "http://www.gdacs.org",
        "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#",
        "dc": "http://purl.org/dc/elements/1.1/",
    }

    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []

    events = []
    for item in channel.findall("item"):

        def get(tag, namespace=None):
            el = item.find(f"{namespace}{tag}" if namespace else tag)
            return el.text.strip() if el is not None and el.text else None

        def get_ns(prefix, tag):
            el = item.find(f"{{{ns[prefix]}}}{tag}")
            return el.text.strip() if el is not None and el.text else None

        event_id_raw = get_ns("gdacs", "eventid")
        event_type = get_ns("gdacs", "eventtype")
        alert_level = get_ns("gdacs", "alertlevel")

        if not event_id_raw or not event_type:
            continue

        event_id = f"gdacs-{event_type.lower()}-{event_id_raw}"
        severity = SEVERITY_MAP.get(alert_level, "blue")
        event_type_norm = TYPE_MAP.get(event_type, event_type.lower())
        category = GDACS_TYPE_TO_CATEGORY.get(event_type)
        is_alert = str(alert_level or "").lower() in {"orange", "red"}

        lat_raw = get_ns("geo", "lat")
        lon_raw = get_ns("geo", "long")

        try:
            lat = float(lat_raw) if lat_raw else None
            lon = float(lon_raw) if lon_raw else None
        except ValueError:
            lat, lon = None, None

        title = get("title") or f"{event_type_norm} event"

        pub_date_raw = get("pubDate")
        started_at = None
        if pub_date_raw:
            try:
                from email.utils import parsedate_to_datetime
                started_at = parsedate_to_datetime(pub_date_raw).replace(tzinfo=None)
            except Exception:
                pass

        country = get_ns("gdacs", "country")
        bbox = get_ns("gdacs", "bbox")
        region = country or _region_from_latlon(lat, lon)

        wind_str = get_ns("gdacs", "maxwind")
        wind_kmh = None
        if wind_str:
            try:
                wind_kmh = int(float(wind_str) * 1.852)  # knots → km/h
            except ValueError:
                pass

        events.append({
            "id": event_id,
            "title": title,
            "type": event_type_norm,
            "category": category,
            "is_alert": is_alert,
            "severity": severity,
            "status": STATUS_MAP.get(severity, "MODERATO"),
            "lat": lat,
            "lon": lon,
            "region": region,
            "wind_kmh": wind_kmh,
            "pressure_hpa": None,
            "started_at": started_at,
        })

    return events


def _region_from_latlon(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    """Stima approssimativa della regione da coordinate."""
    if lat is None or lon is None:
        return None
    if lat > 60:
        return "Artico / Nord Europa"
    if lat > 35 and -30 < lon < 60:
        return "Europa / Medio Oriente"
    if lat > 10 and 60 < lon < 150:
        return "Asia"
    if lat > 10 and -170 < lon < -30:
        return "Nord America"
    if -35 < lat < 10 and -90 < lon < -30:
        return "America Centrale / Caraibi"
    if lat < -10 and lon > 100:
        return "Oceania"
    if lat < -10 and 0 < lon <= 100:
        return "Africa / Oceano Indiano"
    return "Oceano"


def _upsert_event(db: Session, data: dict) -> Event:
    """Inserisce o aggiorna un evento nel DB."""
    event = db.query(Event).filter(Event.id == data["id"]).first()
    if event is None:
        event = Event(**data)
        db.add(event)
        logger.info(f"Nuovo evento: {data['id']} — {data['title']}")
    else:
        for k, v in data.items():
            if k != "id" and v is not None:
                setattr(event, k, v)
        event.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        logger.debug(f"Aggiornato evento: {data['id']}")
    return event


def _upsert_source(db: Session, event_id: str) -> None:
    """Assicura che esista la source GDACS per l'evento."""
    src_id = f"gdacs-official-{event_id}"
    existing = db.query(Source).filter(Source.id == src_id).first()
    if existing is None:
        src = Source(
            id=src_id,
            name="GDACS Official",
            type="ufficiale",
            platform="gdacs",
            url="https://gdacs.org",
            event_id=event_id,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=0,
        )
        db.add(src)
    else:
        existing.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)


def fetch_gdacs_events(db: Session) -> int:
    """
    Entry point del collector GDACS.
    Fetcha il feed RSS, upserta gli eventi e le loro source.
    Ritorna il numero di eventi processati.
    """
    logger.info("GDACS collector: avvio fetch")

    try:
        response = httpx.get(GDACS_FEED_URL, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f"GDACS fetch fallito: {e}")
        return 0

    try:
        events_data = _parse_gdacs_xml(response.text)
    except Exception as e:
        logger.error(f"GDACS parse fallito: {e}")
        return 0

    logger.info(f"GDACS: {len(events_data)} eventi trovati nel feed")

    count = 0
    for data in events_data:
        try:
            _upsert_event(db, data)
            _upsert_source(db, data["id"])
            count += 1
        except Exception as e:
            logger.warning(f"Errore su evento {data.get('id')}: {e}")
            continue

    logger.info(f"GDACS collector: {count} eventi processati")
    return count
