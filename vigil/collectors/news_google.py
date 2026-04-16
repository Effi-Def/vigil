import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.core.news_relevance import MIN_RELEVANCE_SCORE, score_article_relevance
from vigil.core.models import Event, MediaItem, Source
from vigil.core.rss_utils import (
    canonical_url_hash,
    event_region_aliases,
    extract_og_image,
    normalize_text,
    parse_published_datetime,
    parse_rss_feed,
    score_event_match,
)
from vigil.collectors.matcher import clean_media_title, is_semantic_duplicate, normalize_absolute_url

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "Google News RSS"
COLLECTOR_INTERVAL = 25
COLLECTOR_ENABLED = True

ITALIAN_REGION_TOKENS = {
    "abruzzo",
    "basilicata",
    "calabria",
    "campania",
    "emilia-romagna",
    "friuli-venezia giulia",
    "lazio",
    "liguria",
    "lombardia",
    "marche",
    "molise",
    "piemonte",
    "puglia",
    "sardegna",
    "sicilia",
    "toscana",
    "trentino-alto adige",
    "umbria",
    "valle d'aosta",
    "veneto",
    "italia",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "alert", "warning",
    "della", "delle", "dello", "degli", "dell", "della", "alla", "alle", "allo",
    "nella", "nelle", "nel", "nei", "per", "con", "del", "dei", "una", "uno",
    "allerta", "evento", "evento", "meteo", "vigilanza", "dpc",
    "orange", "yellow", "red", "issued", "italy", "italia",
}

PHENOMENON_HINTS = {
    "rain": "pioggia OR nubifragio OR esondazione OR alluvione",
    "flood": "pioggia OR nubifragio OR esondazione OR alluvione",
    "thunderstorm": "temporali OR grandine OR nubifragi",
    "storm": "temporali OR grandine OR nubifragi",
    "wind": "vento OR raffiche OR burrasca",
    "snow": "neve OR ghiaccio OR gelicidio",
    "ice": "ghiaccio OR gelicidio",
}

TYPE_KEYWORDS = {
    "cyclone": "tifone OR uragano OR ciclone",
    "hurricane": "tifone OR uragano OR ciclone",
    "flood": "alluvione OR esondazione OR piena",
    "storm": "temporale OR grandine OR tromba d'aria",
    "volcano": "eruzione OR vulcano OR lava",
    "drought": "siccita OR emergenza idrica",
    "meteoalarm": "allerta meteo OR maltempo",
    "dpc_vigilanza": "allerta meteo OR maltempo",
}

TYPE_TITLE_HINTS = {
    "cyclone": ["tifone", "uragano", "ciclone", "cyclone", "hurricane"],
    "hurricane": ["tifone", "uragano", "ciclone", "cyclone", "hurricane"],
    "flood": ["alluvione", "esondazione", "piena", "flood"],
    "storm": ["temporale", "grandine", "tromba", "storm", "tornado"],
    "volcano": ["eruzione", "vulcano", "lava", "volcano"],
    "drought": ["siccita", "idrica", "drought"],
    "meteoalarm": ["allerta", "meteo", "maltempo", "warning"],
    "dpc_vigilanza": ["allerta", "meteo", "maltempo", "vigilanza"],
}


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _content_hash(link: str) -> str:
    return canonical_url_hash(link)


def _source_id(event_id: str) -> str:
    return f"gnews-{event_id}"


def _is_italian_region(region: str) -> bool:
    if not region:
        return False
    low = region.strip().lower()
    if low in ITALIAN_REGION_TOKENS:
        return True
    return any(tok in low for tok in ITALIAN_REGION_TOKENS)


def _top_words(title: str, limit: int = 3) -> list[str]:
    words = re.findall(r"[A-Za-z\u00C0-\u017F0-9']+", title or "")
    picked = []
    for word in words:
        lw = word.lower()
        if len(lw) < 3 or lw in STOPWORDS:
            continue
        picked.append(word)
        if len(picked) >= limit:
            break
    return picked


def build_query(event: Event) -> str:
    title = str(event.title or "")
    title_words = _top_words(title, limit=3)
    title_norm = normalize_text(title)
    region = str(event.region or "").strip()
    event_type = str(event.type or "").strip().lower()
    type_query = TYPE_KEYWORDS.get(event_type, "maltempo OR disaster OR emergency")
    lang = "it" if _is_italian_region(region) else "en"

    phenomenon_queries = []
    for token, expr in PHENOMENON_HINTS.items():
        if token in title_norm:
            phenomenon_queries.append(expr)
    if phenomenon_queries:
        type_query = " OR ".join(dict.fromkeys(phenomenon_queries))

    parts = []
    if region:
        parts.append(region)
    if title_words:
        parts.append(" ".join(title_words))
    parts.append(type_query)
    parts.append("lingua italiana" if lang == "it" else "english")

    return " ".join(parts)


def _google_feed_url(query: str) -> str:
    encoded = quote_plus(query)
    return f"https://news.google.com/rss/search?q={encoded}&hl=it&gl=IT&ceid=IT:it"


def _source_name(article: dict) -> str:
    raw = (article.get("source") or "").strip()
    if raw:
        return raw
    return "Google News"


def _upsert_source(db: Session, event: Event, query_url: str) -> str:
    src_id = _source_id(str(event.id))
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        src = Source(
            id=src_id,
            name=f"Google News {event.id}",
            type="notizie",
            platform="google_news",
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
    return src_id


def _compute_confidence(event: Event, title: str, article_published=None) -> int:
    confidence = max(60, score_event_match(event, title, title, article_published))
    title_low = normalize_text(title or "")

    if any(alias and alias in title_low for alias in event_region_aliases(event)):
        confidence += 10

    hints = TYPE_TITLE_HINTS.get((event.type or "").lower(), [])
    if any(normalize_text(hint) in title_low for hint in hints):
        confidence += 10

    return min(confidence, 100)


def fetch_google_news(db: Session) -> int:
    """Collect Google News RSS results for top active events."""
    try:
        severity_rank = {"red": 3, "orange": 2, "blue": 1}
        events = db.query(Event).all()
        events = sorted(
            events,
            key=lambda e: (
                severity_rank.get((e.severity or "").lower(), 0),
                e.updated_at or datetime.min,
            ),
            reverse=True,
        )[:10]

        if not events:
            return 0

        total_saved = 0
        og_calls = 0

        for event in events:
            query = build_query(event)
            feed_url = _google_feed_url(query)
            src_id = _upsert_source(db, event, feed_url)
            articles = parse_rss_feed(feed_url)

            saved_for_event = 0
            for article in articles[:15]:
                title = (article.get("title") or "").strip()
                link = (article.get("link") or "").strip()
                description = (article.get("description") or "").strip()
                published = (article.get("published") or "").strip()
                link = normalize_absolute_url(link) or ""
                if not title or not link:
                    continue

                content_hash = _content_hash(link)
                if db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first() is not None:
                    continue
                if is_semantic_duplicate(db, str(event.id), title, media_type="article"):
                    continue

                confidence = _compute_confidence(event, title, parse_published_datetime(published))
                source_name = _source_name(article)
                relevance_score = score_article_relevance(title, description, link, source_name=source_name)
                if relevance_score < MIN_RELEVANCE_SCORE:
                    continue
                thumb_url: Optional[str] = None
                if confidence >= 40:
                    if og_calls > 0:
                        time.sleep(0.3)
                    thumb_url = extract_og_image(link)
                    thumb_url = normalize_absolute_url(thumb_url)
                    og_calls += 1

                source_name = _source_name(article)
                clean_title = clean_media_title(title, source_name=source_name, platform="google_news")

                item = MediaItem(
                    event_id=str(event.id),
                    source_id=src_id,
                    media_url=link,
                    thumb_url=thumb_url,
                    media_type="article",
                    caption=clean_title,
                    author=source_name,
                    lat=None,
                    lon=None,
                    geo_raw=None,
                    captured_at=parse_published_datetime(published),
                    confidence=max(0, min(100, int(round(float(confidence or 0.0))))),
                    relevance_score=round(float(relevance_score), 2),
                    content_hash=content_hash,
                )
                try:
                    with db.begin_nested():
                        db.add(item)
                        db.flush()
                except IntegrityError:
                    continue

                src = db.query(Source).filter(Source.id == src_id).first()
                if src is not None:
                    src.item_count = int(src.item_count or 0) + 1  # type: ignore[assignment]

                saved_for_event += 1
                total_saved += 1

            logger.info(
                f"[google_news] {event.id}: {len(articles[:15])} articoli -> {saved_for_event} salvati"
            )

        return total_saved
    except Exception as exc:
        logger.warning(f"[google_news] errore fetch: {exc}")
        return 0
