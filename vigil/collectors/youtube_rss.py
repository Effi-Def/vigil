"""
Collector Public Video Search — video gratuiti senza API key.
Usa l'API pubblica PeerTube (SepiaSearch) per cercare video rilevanti agli eventi.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.collectors.matcher import clean_media_title, is_semantic_duplicate, normalize_absolute_url
from vigil.core.models import Event, MediaItem, Source
from vigil.core.rss_utils import canonical_url_hash, parse_published_datetime

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Public Video Search"
COLLECTOR_INTERVAL = 30
COLLECTOR_ENABLED = True

MIN_CONFIDENCE = 55
MAX_EVENTS = 12
MAX_ITEMS_PER_EVENT = 8
SEPIA_API = "https://sepiasearch.org/api/v1/search/videos"
YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"
YOUTUBE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}

STRONG_ALERT_VIDEO_TERMS = (
    'alluv', 'flood', 'frana', 'esond', 'terrem', 'sisma', 'quake', 'incend',
    'wildfire', 'evac', 'chius', 'danni', 'croll', 'storm damage', 'snowstorm'
)

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "warning", "issued",
    "allerta", "meteo", "della", "delle", "degli", "dello", "dell", "della",
    "evento", "event", "italia", "italy",
}


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _source_id(event_id: str, platform_key: str = "video_search") -> str:
    return f"{platform_key}-{event_id}"


def _top_words(text: str, limit: int = 3) -> list[str]:
    words = re.findall(r"[A-Za-z\u00C0-\u017F0-9']+", text or "")
    out: list[str] = []
    for w in words:
        lw = w.lower()
        if len(lw) < 3 or lw in STOPWORDS:
            continue
        out.append(w)
        if len(out) >= limit:
            break
    return out


def _is_low_signal_alert(event: Event) -> bool:
    title_norm = str(event.title or '').lower()
    event_type = str(event.type or event.category or '').lower()
    if event_type not in {'meteoalarm', 'dpc_vigilanza', 'storm'}:
        return False
    return not any(token in title_norm for token in STRONG_ALERT_VIDEO_TERMS)


def _build_query(event: Event) -> str:
    chunks: list[str] = []
    region = str(event.region or "").strip()
    category = str(event.category or "").strip()
    title_norm = str(event.title or '').lower()

    if region:
        chunks.append(region)
    if any(tok in title_norm for tok in ('rain', 'flood', 'esond', 'alluv')):
        chunks.extend(['maltempo', 'alluvione'])
    elif any(tok in title_norm for tok in ('storm', 'thunderstorm', 'tempor', 'grandine')):
        chunks.extend(['maltempo', 'temporale'])
    elif 'wind' in title_norm:
        chunks.extend(['vento', 'raffiche'])
    elif any(tok in title_norm for tok in ('snow', 'ice', 'neve', 'ghiaccio')):
        chunks.extend(['neve', 'ghiaccio'])
    else:
        chunks.extend(_top_words(str(event.title or ''), limit=3))

    if category:
        chunks.append(category)
    if not chunks:
        chunks.append(str(event.title or 'disaster'))
    return ' '.join(dict.fromkeys(chunks[:5]))


def _search_public_videos(query: str) -> list[dict[str, Any]]:
    params = {
        "search": query,
        "count": MAX_ITEMS_PER_EVENT,
        "sort": "publishedAt",
    }
    try:
        resp = httpx.get(SEPIA_API, params=params, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if isinstance(data, list):
            return data
        return []
    except Exception as exc:
        logger.warning(f"Public video search error ({query}): {exc}")
        return []


def _search_youtube_public(query: str) -> list[dict[str, Any]]:
    try:
        resp = httpx.get(YOUTUBE_SEARCH_URL, params={"search_query": query}, headers=YOUTUBE_HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text or ""
    except Exception as exc:
        logger.warning(f"YouTube public search error ({query}): {exc}")
        return []

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for match in re.finditer(r'"videoId":"([A-Za-z0-9_-]{11})"', html):
        video_id = match.group(1)
        if video_id in seen_ids:
            continue
        seen_ids.add(video_id)

        window = html[max(0, match.start() - 1200): match.end() + 2200]
        title_match = re.search(r'"title":\{"runs":\[\{"text":"([^"]+)"\}', window)
        if not title_match:
            title_match = re.search(r'"title":\{"simpleText":"([^"]+)"\}', window)
        owner_match = re.search(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"\}', window)
        published_match = re.search(r'"publishedTimeText":\{"simpleText":"([^"]+)"\}', window)

        title = (title_match.group(1) if title_match else '').replace('\\u0026', '&').strip()
        owner = (owner_match.group(1) if owner_match else 'YouTube').replace('\\u0026', '&').strip()
        published = (published_match.group(1) if published_match else '').replace('\\u0026', '&').strip()
        if not title:
            continue

        results.append({
            "name": title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "description": f"{owner} {published}".strip(),
            "thumbnailPath": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "publishedAt": None,
            "platform": "youtube_public",
            "source_name": owner or "YouTube",
        })
        if len(results) >= MAX_ITEMS_PER_EVENT:
            break
    return results


def _confidence_for_event(event: Event, title: str, description: str) -> int:
    hay = f"{title} {description}".lower()
    conf = 20

    region = str(event.region or "").strip().lower()
    category = str(event.category or "").strip().lower()
    title_words = [w.lower() for w in _top_words(str(event.title or ""), limit=3)]

    if region and region in hay:
        conf += 22
    if category and category in hay:
        conf += 14
    for w in title_words:
        if w and w in hay:
            conf += 6

    if any(token in hay for token in STRONG_ALERT_VIDEO_TERMS):
        conf += 12

    return max(0, min(90, conf))


def _upsert_source(db: Session, event: Event, query_url: str, platform_key: str, source_name: str) -> str:
    src_id = _source_id(str(event.id), platform_key)
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        src = Source(
            id=src_id,
            name=source_name,
            type="video",
            platform=platform_key,
            url=query_url,
            event_id=str(event.id),
            last_fetched=_utc_now_naive(),
            item_count=0,
        )
        db.add(src)
        db.flush()
    else:
        src.last_fetched = _utc_now_naive()  # type: ignore[assignment]
        src.url = query_url  # type: ignore[assignment]
        src.name = source_name  # type: ignore[assignment]
        src.platform = platform_key  # type: ignore[assignment]
    return src_id


def fetch_youtube_rss(db: Session) -> int:
    events = db.query(Event).order_by(Event.updated_at.desc()).limit(MAX_EVENTS).all()
    if not events:
        return 0

    total = 0
    for event in events:
        if _is_low_signal_alert(event):
            logger.info(f"[public_video] {event.id}: skip evento troppo generico per ricerca video pubblica")
            continue
        query = _build_query(event)
        items = _search_youtube_public(query) + _search_public_videos(query)

        saved_for_event = 0
        seen_links: set[str] = set()
        for it in items[:MAX_ITEMS_PER_EVENT * 2]:
            title = (it.get("name") or "").strip()
            link = normalize_absolute_url(it.get("url")) or ""
            description = (it.get("description") or "").strip()
            published = str(it.get("publishedAt") or "").strip()
            if not title or not link or link in seen_links:
                continue
            seen_links.add(link)

            source_platform = str(it.get("platform") or "peertube").strip().lower()
            source_name = (it.get("source_name") or ("YouTube" if source_platform == "youtube_public" else "PeerTube")).strip()
            search_url = (
                f"{YOUTUBE_SEARCH_URL}?search_query={query}" if source_platform == "youtube_public"
                else f"{SEPIA_API}?search={query}"
            )
            source_id = _upsert_source(db, event, search_url, source_platform, source_name)

            confidence = _confidence_for_event(event, title, f"{description} {source_name}")
            if source_platform == "youtube_public":
                confidence = min(95, confidence + 6)
            if confidence < MIN_CONFIDENCE:
                continue

            content_hash = canonical_url_hash(link)
            if db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first() is not None:
                continue
            if is_semantic_duplicate(db, str(event.id), title, media_type="video"):
                continue

            clean_title = clean_media_title(title, source_name=source_name, platform=source_platform)
            thumb = normalize_absolute_url(it.get("thumbnailPath") or it.get("thumbnail"))

            item = MediaItem(
                event_id=str(event.id),
                source_id=source_id,
                media_url=link,
                thumb_url=thumb,
                media_type="video",
                caption=clean_title,
                author=source_name,
                lat=None,
                lon=None,
                geo_raw=None,
                captured_at=parse_published_datetime(published),
                confidence=confidence,
                content_hash=content_hash,
            )

            try:
                with db.begin_nested():
                    db.add(item)
                    db.flush()
                src = db.query(Source).filter(Source.id == source_id).first()
                if src is not None:
                    src.item_count = int(src.item_count or 0) + 1  # type: ignore[assignment]
                saved_for_event += 1
                total += 1
            except IntegrityError:
                continue

        logger.info(f"[public_video] {event.id}: {len(items)} trovati -> {saved_for_event} salvati")

    logger.info(f"Public Video Search: salvati {total} video")
    return total
