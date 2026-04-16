"""
Collector Openverse Images — immagini gratuite (licenze aperte) senza API key.
Usa API pubblica Openverse per cercare immagini rilevanti agli eventi attivi.
"""

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.collectors.matcher import is_semantic_duplicate, normalize_absolute_url
from vigil.core.models import Event, MediaItem, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Openverse Images"
COLLECTOR_INTERVAL = 40
COLLECTOR_ENABLED = True

OPENVERSE_API = "https://api.openverse.org/v1/images/"
MAX_PER_EVENT = 8
ACTIVE_DAYS = 10

HEADERS = {"User-Agent": "vigil-monitor/0.2 (disaster monitoring; openverse api)"}


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _event_keywords(event: Event) -> list[str]:
    text = f"{event.title or ''} {event.region or ''}"
    words = [w.lower() for w in re.findall(r"[A-Za-z\u00C0-\u017F0-9']+", text)]
    stop = {
        "warning", "issued", "orange", "red", "blue", "for", "allerta", "meteo",
        "evento", "event", "italy", "italia", "weather",
    }
    out: list[str] = []
    for w in words:
        if len(w) < 4 or w in stop:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= 4:
            break
    if not out and event.category:
        out = [str(event.category).lower()]
    return out


def _build_query(event: Event) -> str:
    title_norm = (event.title or '').lower()
    region = str(event.region or '').strip()

    phenomenon = None
    if any(tok in title_norm for tok in ('rain', 'flood', 'esond', 'alluv')):
        phenomenon = 'flood'
    elif any(tok in title_norm for tok in ('storm', 'thunderstorm', 'tempor', 'grandine')):
        phenomenon = 'storm damage'
    elif 'wind' in title_norm:
        phenomenon = 'strong wind'
    elif any(tok in title_norm for tok in ('snow', 'ice', 'neve', 'ghiaccio')):
        phenomenon = 'snowstorm'

    if region and phenomenon:
        return f"{region} {phenomenon}".strip()

    parts = _event_keywords(event)
    category = str(event.category or "").strip()
    if category:
        parts.append(category)
    if region:
        parts.append(region)
    return " ".join(parts[:4]).strip() or str(event.title or "disaster")


def _upsert_source(db: Session, event_id: str) -> str:
    src_id = f"openverse-{event_id}"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        src = Source(
            id=src_id,
            name="Openverse",
            type="immagini",
            platform="openverse",
            url="https://openverse.org",
            event_id=event_id,
            last_fetched=_utc_now_naive(),
            item_count=0,
        )
        db.add(src)
        db.flush()
    else:
        src.last_fetched = _utc_now_naive()  # type: ignore[assignment]
    return src_id


def _search_openverse(query: str) -> list[dict]:
    params = {
        "q": query,
        "page_size": MAX_PER_EVENT,
        "mature": "false",
    }
    try:
        resp = httpx.get(OPENVERSE_API, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if isinstance(results, list):
            return results
        return []
    except Exception as exc:
        logger.warning(f"Openverse search error ({query}): {exc}")
        return []


def _confidence_for_event(event: Event, title: str) -> int:
    txt = (title or "").lower()
    region = str(event.region or "").strip().lower()
    category = str(event.category or "").strip().lower()
    conf = 45
    if region and region in txt:
        conf += 12
    if category and category in txt:
        conf += 10
    return min(conf, 85)


def fetch_openverse_images(db: Session) -> int:
    cutoff = _utc_now_naive() - timedelta(days=ACTIVE_DAYS)
    events = (
        db.query(Event)
        .filter(Event.updated_at >= cutoff)
        .order_by(Event.updated_at.desc())
        .limit(24)
        .all()
    )

    total = 0
    for event in events:
        query = _build_query(event)
        rows = _search_openverse(query)
        if not rows:
            continue

        source_id = _upsert_source(db, str(event.id))
        saved_for_event = 0
        for row in rows[:MAX_PER_EVENT]:
            url = normalize_absolute_url(row.get("url"))
            thumb = normalize_absolute_url(row.get("thumbnail"))
            title = (row.get("title") or "Openverse image").strip()
            creator = (row.get("creator") or "Openverse").strip()
            if not url:
                continue

            content_hash = hashlib.md5(f"openverse::{event.id}::{url}".encode()).hexdigest()
            if db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first():
                continue
            if is_semantic_duplicate(db, str(event.id), title, media_type="image"):
                continue

            item = MediaItem(
                event_id=str(event.id),
                source_id=source_id,
                media_url=url,
                thumb_url=thumb,
                media_type="image",
                caption=title,
                author=creator,
                lat=getattr(event, "lat", None),
                lon=getattr(event, "lon", None),
                geo_raw=getattr(event, "region", None),
                captured_at=None,
                confidence=_confidence_for_event(event, title),
                content_hash=content_hash,
            )
            try:
                with db.begin_nested():
                    db.add(item)
                    db.flush()
                src = db.query(Source).filter(Source.id == source_id).first()
                if src is not None:
                    src.item_count = int(src.item_count or 0) + 1  # type: ignore[assignment]
                total += 1
                saved_for_event += 1
            except IntegrityError:
                continue

        logger.info(f"[openverse] {event.id}: {len(rows)} trovati -> {saved_for_event} salvati")

    logger.info(f"Openverse: salvate {total} immagini")
    return total
