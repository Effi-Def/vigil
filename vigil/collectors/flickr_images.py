"""
Collector Flickr Images — foto geolocalizzate vicino agli eventi attivi.
"""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.collectors.matcher import normalize_absolute_url
from vigil.core.models import Event, MediaItem, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Flickr Images"
COLLECTOR_INTERVAL = 45
COLLECTOR_ENABLED = True

FLICKR_API = "https://api.flickr.com/services/rest/"

CATEGORY_KEYWORDS = {
    "snow": ["neve", "nevicata", "bufera", "gelo", "ghiaccio", "blizzard"],
    "storm": ["temporale", "tempesta", "vento", "tromba", "grandine"],
    "flood": ["alluvione", "esondazione", "piena", "allagamento", "inondazione"],
    "earthquake": ["terremoto", "sisma", "scossa", "sismico", "magnitudo"],
    "wildfire": ["incendio", "rogo", "fiamme", "bruciato", "wildfire"],
    "extreme_heat": ["caldo", "ondata di calore", "afa", "temperature record"],
}


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _event_date(event: Event) -> datetime:
    started_at = getattr(event, "started_at", None)
    updated_at = getattr(event, "updated_at", None)
    if isinstance(started_at, datetime):
        return started_at
    if isinstance(updated_at, datetime):
        return updated_at
    return _utc_now_naive()


def _upsert_source(db: Session) -> str:
    src_id = "flickr-images"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        src = Source(
            id=src_id,
            name="Flickr",
            type="immagini",
            platform="flickr",
            url="https://www.flickr.com",
            event_id=None,
            last_fetched=_utc_now_naive(),
            item_count=0,
        )
        db.add(src)
        db.flush()
    else:
        src.last_fetched = _utc_now_naive()  # type: ignore[assignment]
    return src_id


def _keyword_for_event(event: Event) -> str:
    category = (event.category or event.type or "").strip().lower()
    return (CATEGORY_KEYWORDS.get(category) or ["meteo"])[0]


def _flickr_search(api_key: str, lat: float, lon: float, tag: str) -> list[dict[str, Any]]:
    params = {
        "method": "flickr.photos.search",
        "api_key": api_key,
        "lat": lat,
        "lon": lon,
        "radius": 20,
        "radius_units": "km",
        "tags": tag,
        "sort": "date-posted-desc",
        "per_page": 5,
        "extras": "url_m,geo,date_taken,owner_name",
        "format": "json",
        "nojsoncallback": 1,
    }
    try:
        resp = httpx.get(FLICKR_API, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        return (payload.get("photos") or {}).get("photo") or []
    except Exception as exc:
        logger.warning(f"Flickr search fallita ({lat},{lon}): {exc}")
        return []


def fetch_flickr_images(db: Session) -> int:
    api_key = (os.getenv("FLICKR_API_KEY") or "").strip()
    if not api_key:
        logger.info("Flickr collector: FLICKR_API_KEY non impostata, skip")
        return 0

    src_id = _upsert_source(db)
    cutoff_events = _utc_now_naive() - timedelta(days=10)
    events = (
        db.query(Event)
        .filter(Event.lat.isnot(None), Event.lon.isnot(None))
        .filter(Event.updated_at >= cutoff_events)
        .order_by(Event.updated_at.desc())
        .limit(30)
        .all()
    )

    total = 0
    for event in events:
        lat_raw = getattr(event, "lat", None)
        lon_raw = getattr(event, "lon", None)
        if lat_raw is None or lon_raw is None:
            continue
        lat = float(lat_raw)
        lon = float(lon_raw)
        tag = _keyword_for_event(event)
        photos = _flickr_search(api_key, lat, lon, tag)
        if not photos:
            continue

        event_dt = _event_date(event)
        max_photo_age = event_dt + timedelta(days=7)

        for photo in photos:
            url = normalize_absolute_url(photo.get("url_m"))
            if not url:
                continue

            date_taken_raw = (photo.get("datetaken") or "").strip()
            captured_at = None
            if date_taken_raw:
                try:
                    captured_at = datetime.fromisoformat(date_taken_raw.replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    captured_at = None

            # Skip photos older than 7 days relative to event date.
            if captured_at is not None and captured_at < (event_dt - timedelta(days=7)):
                continue
            if captured_at is not None and captured_at > max_photo_age:
                continue

            content_hash = hashlib.md5(f"flickr::{url}".encode()).hexdigest()
            if db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first():
                continue

            title = (photo.get("title") or "").strip() or f"Flickr foto - {event.region or event.id}"
            author = (photo.get("ownername") or "Flickr").strip()[:120]
            item = MediaItem(
                event_id=event.id,
                source_id=src_id,
                media_url=url,
                thumb_url=url,
                media_type="image",
                caption=title,
                author=author,
                lat=lat,
                lon=lon,
                geo_raw=event.region,
                captured_at=captured_at,
                confidence=50,
                content_hash=content_hash,
            )
            try:
                with db.begin_nested():
                    db.add(item)
                    db.flush()
                src = db.query(Source).filter(Source.id == src_id).first()
                if src is not None:
                    src.item_count = int(src.item_count or 0) + 1  # type: ignore[assignment]
                total += 1
            except IntegrityError:
                continue

    logger.info(f"Flickr collector: {total} immagini salvate")
    return total
