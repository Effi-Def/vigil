"""
Collector Reddit RSS — subreddit su disastri naturali e meteo.
Legge i feed RSS pubblici di Reddit (nessuna API key), matcha i post
agli eventi attivi e li salva come MediaItem (con possibile immagine).
"""
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.core.models import MediaItem, Source
from vigil.collectors.matcher import clean_media_title, match_event, normalize_absolute_url
from vigil.core.rss_utils import (
    canonical_url_hash,
    domain_name,
    extract_image_from_description,
    parse_published_datetime,
    parse_rss_feed,
)

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Reddit Disaster RSS"
COLLECTOR_INTERVAL = 25
COLLECTOR_ENABLED = True

# Subreddit con contenuto rilevante: foto, video, report da utenti
SUBREDDITS = [
    "NaturalDisasters",
    "hurricane",
    "floods",
    "earthquake",
    "wildfire",
    "volcanoes",
    "weather",
    "extremeweather",
    "tornado",
]

HEADERS = {
    "User-Agent": "vigil-monitor/0.2 (weather monitoring)",
}

# Soglia minima di confidence per salvare il post
MIN_CONFIDENCE = 0.30


def _rss_url(subreddit: str) -> str:
    return f"https://www.reddit.com/r/{subreddit}/new/.rss?limit=25"


def _extract_reddit_image(description: str) -> Optional[str]:
    """Cerca immagini nei tag <img> dell'HTML del post Reddit."""
    if not description:
        return None
    # Reddit include thumbnail come <img> nell'HTML
    m = re.search(r'<img[^>]+src=["\']([^"\']+\.(?:jpg|jpeg|png|gif|webp))["\']', description, re.IGNORECASE)
    if m:
        url = m.group(1)
        if url.startswith("http") and (
            "preview.redd.it" in url or "i.redd.it" in url or "imgur.com" in url
        ):
            return normalize_absolute_url(url)
    return normalize_absolute_url(extract_image_from_description(description))


def _source_id(subreddit: str) -> str:
    return f"reddit-r-{subreddit.lower()}"


def _upsert_source(db: Session, subreddit: str) -> str:
    src_id = _source_id(subreddit)
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        db.add(Source(
            id=src_id,
            name=f"r/{subreddit}",
            type="social",
            platform="reddit",
            url=f"https://reddit.com/r/{subreddit}",
            event_id=None,
            last_fetched=datetime.now(timezone.utc).replace(tzinfo=None),
            item_count=0,
        ))
        db.flush()
    else:
        src.last_fetched = datetime.now(timezone.utc).replace(tzinfo=None)
    return src_id


def fetch_reddit_rss(db: Session) -> int:
    """Fetcha i feed RSS dei subreddit e collega i post agli eventi attivi."""
    logger.info("Reddit RSS: avvio fetch")
    total = 0

    for subreddit in SUBREDDITS:
        src_id = _upsert_source(db, subreddit)
        feed_url = _rss_url(subreddit)

        try:
            items = parse_rss_feed(feed_url)
        except Exception as e:
            logger.warning(f"Reddit r/{subreddit} fetch fallito: {e}")
            continue

        saved = 0
        for item in items[:20]:
            title = (item.get("title") or "").strip()
            link = (item.get("link") or "").strip()
            description = (item.get("description") or "").strip()
            published = (item.get("published") or "").strip()
            link = normalize_absolute_url(link) or ""

            if not title or not link:
                continue

            text = f"{title} {description[:500]}"
            event_id, confidence = match_event(db, text, title)
            if not event_id or confidence < MIN_CONFIDENCE:
                continue

            content_hash = canonical_url_hash(link)
            if db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first():
                continue

            thumb_url = _extract_reddit_image(description)
            clean_title = clean_media_title(title, source_name=f"r/{subreddit}", platform="reddit")

            item_obj = MediaItem(
                event_id=event_id,
                source_id=src_id,
                media_url=link,
                thumb_url=thumb_url,
                media_type="article",
                caption=f"{clean_title}\n[r/{subreddit}]",
                author=f"r/{subreddit}",
                lat=None,
                lon=None,
                geo_raw=None,
                captured_at=parse_published_datetime(published),
                confidence=max(0, min(100, int(round(float(confidence or 0.0) * 100)))),
                content_hash=content_hash,
            )
            try:
                with db.begin_nested():
                    db.add(item_obj)
                    db.flush()
                src = db.query(Source).filter(Source.id == src_id).first()
                if src:
                    src.item_count = int(src.item_count or 0) + 1
                saved += 1
                total += 1
            except IntegrityError:
                continue

        logger.info(f"[reddit] r/{subreddit}: {len(items)} post → {saved} salvati")
        time.sleep(0.5)  # fair use: non spammare Reddit

    logger.info(f"Reddit RSS: {total} post salvati in totale")
    return total
