"""
Collector NOAA NHC — National Hurricane Center.
Fetcha i feed RSS degli uragani/cicloni attivi per i bacini Atlantico,
Pacifico Est e Pacifico Centro. Ogni advisory diventa/aggiorna un Event.
Fonte pubblica, nessuna API key.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.models import Event, Source
from vigil.core.rss_utils import parse_rss_feed

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "NOAA NHC"
COLLECTOR_INTERVAL = 20
COLLECTOR_ENABLED = True

# Ogni slot può contenere un ciclone attivo; NHC ne supporta fino a 5 per bacino
NHC_FEEDS = [
    # Atlantico
    "https://www.nhc.noaa.gov/nhc_at1.xml",
    "https://www.nhc.noaa.gov/nhc_at2.xml",
    "https://www.nhc.noaa.gov/nhc_at3.xml",
    "https://www.nhc.noaa.gov/nhc_at4.xml",
    "https://www.nhc.noaa.gov/nhc_at5.xml",
    # Pacifico Est
    "https://www.nhc.noaa.gov/nhc_ep1.xml",
    "https://www.nhc.noaa.gov/nhc_ep2.xml",
    "https://www.nhc.noaa.gov/nhc_ep3.xml",
    "https://www.nhc.noaa.gov/nhc_ep4.xml",
    "https://www.nhc.noaa.gov/nhc_ep5.xml",
    # Pacifico Centro
    "https://www.nhc.noaa.gov/nhc_cp1.xml",
    "https://www.nhc.noaa.gov/nhc_cp2.xml",
]

# Regex per estrarre lat/lon dal testo advisory NHC
# Esempio: "18.5N 68.5W" o "12.3°N 85.6°W"
_LAT_LON_RE = re.compile(
    r"(\d+\.?\d*)\s*[°]?\s*([NS])\s+(\d+\.?\d*)\s*[°]?\s*([EW])",
    re.IGNORECASE,
)

# Regex per estrarre la categoria/intensità
_INTENSITY_RE = re.compile(
    r"(Category\s*[1-5]|Hurricane|Tropical Storm|Tropical Depression|"
    r"Typhoon|Super Typhoon|Subtropical Storm)",
    re.IGNORECASE,
)

STATUS_MAP = {"red": "CRITICO", "orange": "ATTENZIONE", "blue": "MODERATO"}


def _parse_latlon(text: str) -> tuple[Optional[float], Optional[float]]:
    m = _LAT_LON_RE.search(text or "")
    if not m:
        return None, None
    lat = float(m.group(1)) * (-1 if m.group(2).upper() == "S" else 1)
    lon = float(m.group(3)) * (-1 if m.group(4).upper() == "W" else 1)
    return lat, lon


def _intensity_to_severity(text: str) -> str:
    t = (text or "").lower()
    if "category 4" in t or "category 5" in t or "super typhoon" in t:
        return "red"
    if any(w in t for w in ["category 1", "category 2", "category 3", "hurricane", "typhoon"]):
        return "orange"
    return "blue"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:50]


def _process_feed(db: Session, feed_url: str) -> int:
    items = parse_rss_feed(feed_url)
    if not items:
        return 0

    # Il primo item è l'advisory più recente
    item = items[0]
    title_raw = (item.get("title") or "").strip()
    description = (item.get("description") or "").strip()
    link = (item.get("link") or feed_url).strip()
    published_raw = (item.get("published") or "").strip()

    if not title_raw:
        return 0

    lat, lon = _parse_latlon(description)
    if lat is None:
        lat, lon = _parse_latlon(title_raw)

    severity = _intensity_to_severity(f"{title_raw} {description}")

    started_at = None
    if published_raw:
        try:
            from email.utils import parsedate_to_datetime
            started_at = parsedate_to_datetime(published_raw).replace(tzinfo=None)
        except Exception:
            pass

    event_id = f"nhc-{_slug(title_raw)}"

    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        event = Event(
            id=event_id,
            title=title_raw[:200],
            type="cyclone",
            category=EventCategory.cyclone.value,
            is_alert=True,
            severity=severity,
            status=STATUS_MAP[severity],
            lat=lat,
            lon=lon,
            region=None,
            wind_kmh=None,
            pressure_hpa=None,
            started_at=started_at,
        )
        db.add(event)
        logger.info(f"Nuovo ciclone NHC: {event_id} — {title_raw}")
    else:
        event.title = title_raw[:200]
        event.category = EventCategory.cyclone.value
        event.is_alert = True
        event.severity = severity
        event.status = STATUS_MAP[severity]
        if lat is not None:
            event.lat = lat
        if lon is not None:
            event.lon = lon
        event.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    src_id = f"nhc-src-{event_id}"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        db.add(Source(
            id=src_id,
            name="NOAA NHC",
            type="ufficiale",
            platform="noaa_nhc",
            url=link,
            event_id=event_id,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=len(items),
        ))
    else:
        src.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)
        src.item_count = len(items)

    return 1


def fetch_noaa_nhc(db: Session) -> int:
    """Fetcha i feed RSS NHC per tutti i bacini e aggiorna i cicloni attivi."""
    logger.info("NOAA NHC: avvio fetch cicloni attivi")
    count = 0
    for feed_url in NHC_FEEDS:
        try:
            n = _process_feed(db, feed_url)
            count += n
        except Exception as e:
            logger.debug(f"NHC feed {feed_url}: {e}")
            continue
    logger.info(f"NOAA NHC: {count} cicloni processati")
    return count
