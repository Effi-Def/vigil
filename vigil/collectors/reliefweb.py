"""
Collector ReliefWeb — report umanitari globali.
Fonte: api.reliefweb.int — API pubblica UNOCHA, nessuna API key.
Fetcha i report recenti su disastri naturali e li collega agli eventi attivi.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.collectors.matcher import clean_media_title, normalize_absolute_url
from vigil.core.models import Event, MediaItem, Source
from vigil.core.rss_utils import canonical_url_hash

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "ReliefWeb Reports"
COLLECTOR_INTERVAL = 60
COLLECTOR_ENABLED = True

RELIEFWEB_URL = "https://api.reliefweb.int/v1/reports"

# Filtra solo report su disastri naturali e meteo
DISASTER_TYPES = [
    "Flood", "Earthquake", "Cyclone", "Volcanic activity",
    "Drought", "Wildfire", "Landslide", "Tsunami",
    "Storm surge", "Severe local storm", "Extreme temperature",
    "Snow avalanche", "Cold wave", "Heat wave",
]

DEFAULT_CONFIDENCE = 60


def _build_query() -> dict:
    return {
        "limit": 30,
        "sort": ["date.created:desc"],
        "filter": {
            "operator": "AND",
            "conditions": [
                {"field": "status", "value": "published"},
                {
                    "field": "disaster_type.name",
                    "value": DISASTER_TYPES,
                    "operator": "OR",
                },
            ],
        },
        "fields": {
            "include": [
                "title",
                "url",
                "date.created",
                "primary_country.name",
                "disaster_type.name",
                "source.name",
                "body-html",
            ]
        },
    }


def _extract_image_from_html(html: str) -> Optional[str]:
    import re
    if not html:
        return None
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        url = m.group(1).strip()
        if url.startswith("http"):
            return url
    return None


def _upsert_source(db: Session) -> str:
    src_id = "reliefweb-reports"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        db.add(Source(
            id=src_id,
            name="ReliefWeb",
            type="ufficiale",
            platform="reliefweb",
            url="https://reliefweb.int",
            event_id=None,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=0,
        ))
        db.flush()
    else:
        src.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)
    return src_id


def _upsert_reliefweb_event(
    db: Session,
    event_id: str,
    title: str,
    region: Optional[str],
    started_at: Optional[datetime],
) -> None:
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        db.add(
            Event(
                id=event_id,
                title=title[:200],
                type=EventCategory.humanitarian.value,
                category=EventCategory.humanitarian.value,
                is_alert=False,
                severity="blue",
                status="MODERATO",
                lat=None,
                lon=None,
                region=(region or "Globale")[:120],
                wind_kmh=None,
                pressure_hpa=None,
                started_at=started_at,
            )
        )
    else:
        event.title = title[:200]
        event.type = EventCategory.humanitarian.value
        event.category = EventCategory.humanitarian.value
        event.is_alert = False
        event.severity = "blue"
        event.status = "MODERATO"
        event.region = (region or "Globale")[:120]
        if started_at is not None:
            event.started_at = started_at
        event.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)


def fetch_reliefweb(db: Session) -> int:
    """Fetcha report umanitari da ReliefWeb e li collega agli eventi attivi."""
    logger.info("ReliefWeb: avvio fetch report")

    try:
        resp = httpx.post(
            RELIEFWEB_URL,
            json=_build_query(),
            params={"appname": "vigil-monitor"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"ReliefWeb fetch fallito: {e}")
        return 0

    reports = data.get("data", [])
    logger.info(f"ReliefWeb: {len(reports)} report recuperati")

    src_id = _upsert_source(db)
    total = 0

    for report in reports:
        fields = report.get("fields", {})
        title = (fields.get("title") or "").strip()
        url_data = fields.get("url") or {}
        url = url_data if isinstance(url_data, str) else url_data.get("canonical") or url_data.get("") or ""
        if not url and isinstance(report.get("href"), str):
            url = report["href"]
        url = normalize_absolute_url(url) or ""

        date_obj = fields.get("date") or {}
        date_str = date_obj.get("created") if isinstance(date_obj, dict) else None
        captured_at = None
        if date_str:
            try:
                captured_at = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                pass

        country_obj = fields.get("primary_country") or {}
        country = country_obj.get("name") if isinstance(country_obj, dict) else str(country_obj)

        disaster_types = fields.get("disaster_type") or []
        if isinstance(disaster_types, list):
            dtype = disaster_types[0].get("name") if disaster_types else "disastro"
        else:
            dtype = str(disaster_types)

        source_obj = fields.get("source") or []
        source_name = source_obj[0].get("name") if source_obj else "ReliefWeb"

        body_html = fields.get("body-html") or ""
        thumb_url = normalize_absolute_url(_extract_image_from_html(body_html))

        if not title or not url:
            continue

        content_hash = canonical_url_hash(url)
        event_id = f"reliefweb-hum-{content_hash[:16]}"

        _upsert_reliefweb_event(db, event_id, title, country, captured_at)

        if db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first():
            continue

        clean_title = clean_media_title(title, source_name=source_name, platform="reliefweb")
        caption = f"{clean_title}\n{dtype} · {country}"
        item = MediaItem(
            event_id=event_id,
            source_id=src_id,
            media_url=url,
            thumb_url=thumb_url,
            media_type="article",
            caption=caption,
            author=source_name,
            lat=None,
            lon=None,
            geo_raw=country,
            captured_at=captured_at,
            confidence=DEFAULT_CONFIDENCE,
            content_hash=content_hash,
        )
        try:
            with db.begin_nested():
                db.add(item)
                db.flush()
            src = db.query(Source).filter(Source.id == src_id).first()
            if src:
                src.item_count = int(src.item_count or 0) + 1
            total += 1
        except IntegrityError:
            continue

    logger.info(f"ReliefWeb: {total} report collegati agli eventi")
    return total
