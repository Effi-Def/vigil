"""
Collector Wikimedia Commons — immagini geolocalizzate vicino agli eventi.
Usa la MediaWiki API pubblica (nessuna chiave) per cercare file multimediali
con coordinate entro un raggio dall'epicentro di ogni evento attivo.
Produce MediaItem con immagini reali geotaggate.
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import quote

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.collectors.matcher import normalize_absolute_url
from vigil.core.models import Event, MediaItem, Source

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Wikimedia Commons Images"
COLLECTOR_INTERVAL = 45
COLLECTOR_ENABLED = True

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Raggio di ricerca in metri attorno alle coordinate dell'evento
SEARCH_RADIUS_M = 150_000   # 150 km

# Max immagini per evento
MAX_IMAGES_PER_EVENT = 8

# Enrich solo eventi aggiornati negli ultimi N giorni
ACTIVE_DAYS = 10

HEADERS = {"User-Agent": "vigil-monitor/0.2 (weather/disaster monitoring; commons api)"}


def _event_keywords(event: Event) -> list[str]:
    base = f"{event.title or ''} {event.region or ''}"
    words = [w.lower() for w in re.findall(r"[A-Za-z\u00C0-\u017F0-9']+", base)]
    stop = {
        "warning", "issued", "for", "orange", "red", "blue", "allerta", "meteo",
        "italy", "italia", "evento", "wind", "storm", "weather",
    }
    out: list[str] = []
    for w in words:
        if len(w) < 4 or w in stop:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= 4:
            break
    return out


def _nearby_probes(lat: float, lon: float) -> list[tuple[float, float]]:
    # Commons geosearch allows only 10km radius, so probe nearby points.
    step = 0.22
    return [
        (lat, lon),
        (lat + step, lon),
        (lat - step, lon),
        (lat, lon + step),
        (lat, lon - step),
    ]


def _geo_search(lat: float, lon: float, radius_m: int = SEARCH_RADIUS_M) -> list[str]:
    """Cerca titoli di file multimediali geolocalizzati vicino a lat/lon."""
    titles: list[str] = []
    for probe_lat, probe_lon in _nearby_probes(lat, lon):
        params = {
            "action": "query",
            "list": "geosearch",
            "gscoord": f"{probe_lat}|{probe_lon}",
            "gsradius": min(radius_m, 10_000),
            "gslimit": MAX_IMAGES_PER_EVENT,
            "gsnamespace": 6,
            "format": "json",
        }
        try:
            resp = httpx.get(COMMONS_API, params=params, headers=HEADERS, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            for page in data.get("query", {}).get("geosearch", []):
                title = page.get("title")
                if isinstance(title, str) and title not in titles:
                    titles.append(title)
                    if len(titles) >= MAX_IMAGES_PER_EVENT * 2:
                        return titles
        except Exception as e:
            logger.debug(f"Wikimedia geosearch fallita ({probe_lat},{probe_lon}): {e}")
    return titles


def _keyword_search(event: Event) -> list[str]:
    keywords = _event_keywords(event)
    if not keywords:
        return []
    query = " ".join(keywords[:3])
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srnamespace": 6,
        "srlimit": MAX_IMAGES_PER_EVENT,
        "format": "json",
    }
    try:
        resp = httpx.get(COMMONS_API, params=params, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        return [r.get("title") for r in resp.json().get("query", {}).get("search", []) if isinstance(r.get("title"), str)]
    except Exception as e:
        logger.debug(f"Wikimedia keyword search fallita ({query}): {e}")
        return []


def _get_image_urls(titles: list[str]) -> list[dict]:
    """Recupera URL immagine e metadati per una lista di titoli File:."""
    if not titles:
        return []
    params = {
        "action": "query",
        "titles": "|".join(titles[:MAX_IMAGES_PER_EVENT]),
        "prop": "imageinfo",
        "iiprop": "url|dimensions|timestamp|extmetadata",
        "iiurlwidth": 480,
        "format": "json",
    }
    try:
        resp = httpx.get(COMMONS_API, params=params, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        results = []
        for page in pages.values():
            page_title = page.get("title", "")
            for ii in page.get("imageinfo", []):
                url = normalize_absolute_url(ii.get("url", ""))
                thumb = normalize_absolute_url(ii.get("thumburl") or ii.get("url"))
                ts = ii.get("timestamp")
                ext = ii.get("extmetadata", {})
                description = ext.get("ImageDescription", {}).get("value", "")
                author = ext.get("Artist", {}).get("value", "Wikimedia Commons")
                # Strip HTML from author/description
                import re
                author = re.sub(r"<[^>]+>", "", author or "").strip()[:80]
                description = re.sub(r"<[^>]+>", "", description or "").strip()[:300]
                if url:
                    results.append({
                        "title": page_title,
                        "url": url,
                        "thumb": thumb,
                        "timestamp": ts,
                        "author": author or "Wikimedia Commons",
                        "caption": description or page_title,
                    })
        return results
    except Exception as e:
        logger.debug(f"Wikimedia imageinfo fallita: {e}")
        return []


def _upsert_source(db: Session, event_id: str) -> str:
    src_id = f"wikimedia-{event_id}"
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        db.add(Source(
            id=src_id,
            name="Wikimedia Commons",
            type="immagini",
            platform="wikimedia",
            url="https://commons.wikimedia.org",
            event_id=event_id,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=0,
        ))
        db.flush()
    else:
        src.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)
    return src_id


def fetch_wikimedia_images(db: Session) -> int:
    """Cerca immagini geotaggate su Wikimedia Commons vicino agli eventi attivi."""
    logger.info("Wikimedia: avvio ricerca immagini geolocalizzate")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=ACTIVE_DAYS)
    events = (
        db.query(Event)
        .filter(Event.lat.isnot(None), Event.lon.isnot(None))
        .filter(Event.updated_at >= cutoff)
        .order_by(Event.updated_at.desc())
        .limit(20)
        .all()
    )

    logger.info(f"Wikimedia: {len(events)} eventi con coordinate da arricchire")
    total = 0

    for event in events:
        titles = _geo_search(event.lat, event.lon)
        if not titles:
            titles = _keyword_search(event)
        if not titles:
            time.sleep(0.3)
            continue

        images = _get_image_urls(titles)
        if not images:
            time.sleep(0.3)
            continue

        src_id = _upsert_source(db, event.id)

        for img in images:
            content_hash = hashlib.md5(img["url"].encode()).hexdigest()
            if db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first():
                continue

            captured_at = None
            if img.get("timestamp"):
                try:
                    captured_at = datetime.fromisoformat(
                        img["timestamp"].replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    pass

            item = MediaItem(
                event_id=event.id,
                source_id=src_id,
                media_url=img["url"],
                thumb_url=img["thumb"],
                media_type="image",
                caption=img["caption"],
                author=img["author"],
                lat=event.lat,
                lon=event.lon,
                geo_raw=event.region,
                captured_at=captured_at,
                confidence=72,   # geotaggate = alta confidence spaziale
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

        logger.info(f"[wikimedia] {event.id}: {len(images)} immagini → salvate")
        time.sleep(0.5)   # fair use API

    logger.info(f"Wikimedia: {total} immagini salvate in totale")
    return total
