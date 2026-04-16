"""
Collector NASA FIRMS active fires (VIIRS NOAA-20 NRT).
Fonte: CSV FIRMS API, richiede MAP_KEY via env FIRMS_MAP_KEY.
"""
import csv
import logging
import os
from datetime import datetime, timezone
from io import StringIO

import httpx
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.models import Event, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "NASA FIRMS"
COLLECTOR_INTERVAL = 20
COLLECTOR_ENABLED = True

STATUS_MAP = {"red": "CRITICO", "orange": "ATTENZIONE", "blue": "MODERATO"}


def _severity_from_frp(frp: float) -> str:
    if frp >= 120:
        return "red"
    if frp >= 40:
        return "orange"
    return "blue"


def _parse_started_at(acq_date: str, acq_time: str | None) -> datetime | None:
    if not acq_date:
        return None
    hhmm = (acq_time or "0000").zfill(4)
    ts = f"{acq_date} {hhmm[:2]}:{hhmm[2:]}"
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M")
    except Exception:
        return None


def _event_id(lat: float, lon: float, acq_date: str) -> str:
    lat_key = int(round(lat * 1000))
    lon_key = int(round(lon * 1000))
    return f"firms-wf-{acq_date}-{lat_key}-{lon_key}"


def _upsert_source(db: Session) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    src = db.query(Source).filter(Source.id == "nasa-firms").first()
    if src is None:
        db.add(
            Source(
                id="nasa-firms",
                name="NASA FIRMS",
                type="ufficiale",
                platform="nasa_firms",
                url="https://firms.modaps.eosdis.nasa.gov",
                event_id=None,
                last_fetched=now,
                item_count=0,
            )
        )
    else:
        src.last_fetched = now  # type: ignore[assignment]


def fetch_nasa_firms(db: Session) -> int:
    map_key = (os.getenv("FIRMS_MAP_KEY") or "").strip()
    if not map_key:
        logger.info("NASA FIRMS: FIRMS_MAP_KEY non impostata, skip")
        return 0

    url = (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{map_key}/VIIRS_NOAA20_NRT/world/1"
    )

    try:
        response = httpx.get(url, timeout=25)
        response.raise_for_status()
    except Exception as exc:
        logger.error(f"NASA FIRMS fetch fallito: {exc}")
        return 0

    rows = csv.DictReader(StringIO(response.text))
    _upsert_source(db)

    added = 0
    for row in rows:
        try:
            lat = float(row.get("latitude", ""))
            lon = float(row.get("longitude", ""))
            acq_date = (row.get("acq_date") or "").strip()
            if not acq_date:
                continue
            frp = float(row.get("frp") or 0.0)
        except Exception:
            continue

        ev_id = _event_id(lat, lon, acq_date)
        if db.query(Event).filter(Event.id == ev_id).first() is not None:
            continue

        severity = _severity_from_frp(frp)
        started_at = _parse_started_at(acq_date, row.get("acq_time"))
        event = Event(
            id=ev_id,
            title=f"Incendio attivo FIRMS ({acq_date})",
            type=EventCategory.wildfire.value,
            category=EventCategory.wildfire.value,
            is_alert=False,
            severity=severity,
            status=STATUS_MAP[severity],
            lat=lat,
            lon=lon,
            region="Globale",
            wind_kmh=None,
            pressure_hpa=None,
            started_at=started_at,
        )
        db.add(event)
        added += 1

    src = db.query(Source).filter(Source.id == "nasa-firms").first()
    if src is not None:
        src.item_count = int(src.item_count or 0) + added  # type: ignore[assignment]

    logger.info(f"NASA FIRMS: {added} eventi incendio processati")
    return added
