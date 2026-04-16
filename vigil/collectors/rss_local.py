import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from vigil.core.categories import EventCategory
from vigil.core.geo import extract_coordinates
from vigil.core.models import Event, MediaItem, Source
from vigil.core.news_relevance import MIN_RELEVANCE_SCORE, score_article_relevance
from vigil.core.rss_utils import (
    canonical_url_hash,
    domain_name,
    extract_og_image,
    normalize_text,
    parse_published_datetime,
    parse_rss_feed,
)
from vigil.core.rss_utils import extract_image_from_description
from vigil.collectors.matcher import clean_media_title, is_semantic_duplicate, match_event, normalize_absolute_url

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "RSS Testate Locali"
COLLECTOR_INTERVAL = 20
COLLECTOR_ENABLED = True

REGIONAL_RSS = {
    "emilia-romagna": [
        "https://www.ravennatoday.it/rss",
        "https://www.forlitoday.it/rss",
        "https://www.cesenatoday.it/rss",
        "https://corrieredibologna.corriere.it/rss/",
        "https://www.ilrestodelcarlino.it/rss/bologna",
        "https://bologna.repubblica.it/rss/",
    ],
    "veneto": [
        "https://www.veneziatoday.it/rss",
        "https://www.vicenzatoday.it/rss",
        "https://www.padovaoggi.it/rss",
        "https://www.trevisotoday.it/rss",
    ],
    "toscana": [
        "https://www.firenzetoday.it/rss",
        "https://www.pisatoday.it/rss",
        "https://www.grossetonotizie.com/feed",
    ],
    "lombardia": [
        "https://www.milanotoday.it/rss",
        "https://www.bresciatoday.it/rss",
        "https://www.bergamotoday.it/rss",
    ],
    "sicilia": [
        "https://www.palermotoday.it/rss",
        "https://www.cataniatoday.it/rss",
    ],
    "puglia": [
        "https://www.baritoday.it/rss",
        "https://www.foggiatoday.it/rss",
        "https://www.tarantooggi.it/feed/",
        "https://www.lecceprima.it/rss",
    ],
    "campania": [
        "https://www.napolitoday.it/rss",
        "https://www.salernotoday.it/rss",
        "https://www.casertanotizie.it/feed/",
    ],
    "lazio": [
        "https://www.romatoday.it/rss",
        "https://www.latinatoday.it/rss",
        "https://www.frosinonetoday.it/rss",
    ],
    "piemonte": [
        "https://www.torinotoday.it/rss",
        "https://www.alessandrianews.it/rss",
    ],
    "liguria": [
        "https://www.genovatoday.it/rss",
        "https://www.ivg.it/feed/",
    ],
    "calabria": [
        "https://www.calabriatoday.it/rss",
        "https://www.reggiotoday.it/rss",
        "https://www.cosenzatoday.it/rss",
    ],
    "abruzzo": [
        "https://www.chietitoday.it/rss",
        "https://www.ilpescara.it/rss",
        "https://www.teramo.it/rss",
    ],
    "sardegna": [
        "https://www.cagliarinotizie.it/rss",
        "https://www.sardiniapost.it/feed/",
    ],
    "marche": [
        "https://www.anconatoday.it/rss",
        "https://www.pesaronotizie.com/feed/",
    ],
    "molise": [
        "https://www.primonumero.it/feed/",
        "https://termoli.net/feed/",
        "https://www.molisenetwork.net/feed/",
    ],
    "umbria": [
        "https://www.perugiatoday.it/rss",
    ],
    "nazionale": [
        "https://www.ansa.it/sito/notizie/cronaca/cronaca_rss.xml",
        "https://www.ansa.it/sito/ansait_rss.xml",
        "https://www.meteoweb.eu/feed/",
        "https://www.rainews.it/rss/rainews/rss2.0.xml",
        "https://www.repubblica.it/rss/homepage/rss2.0.xml",
    ],
}

WILDFIRE_KEYWORDS = (
    "incendio",
    "incendi",
    "rogo",
    "roghi",
    "boschivo",
    "boschivi",
    "fiamme",
    "wildfire",
    "forest fire",
)

WILDFIRE_STRONG_HINTS = (
    "maxi",
    "vasto",
    "grande",
    "fuori controllo",
    "evacu",
    "canadair",
    "vigili del fuoco",
)

REGION_LABELS = {
    "emilia-romagna": "Emilia-Romagna",
    "veneto": "Veneto",
    "toscana": "Toscana",
    "lombardia": "Lombardia",
    "friuli-venezia giulia": "Friuli-Venezia Giulia",
    "sicilia": "Sicilia",
    "puglia": "Puglia",
    "campania": "Campania",
    "lazio": "Lazio",
    "piemonte": "Piemonte",
    "liguria": "Liguria",
    "calabria": "Calabria",
    "abruzzo": "Abruzzo",
    "sardegna": "Sardegna",
    "marche": "Marche",
    "molise": "Molise",
    "umbria": "Umbria",
    "nazionale": "Italia",
}

REGION_ALIASES = {
    "Emilia-Romagna": ["emilia romagna", "emilia-romagna", "bologna", "ravenna", "forli", "cesena"],
    "Veneto": ["veneto", "venet", "padova", "venezia", "vicenza", "treviso"],
    "Toscana": ["toscana", "toscan", "chianciano", "firenze", "siena", "pisa", "lucca"],
    "Lombardia": ["lombardia", "lombard", "milano", "legnano", "bergamo", "brescia"],
    "Friuli-Venezia Giulia": ["friuli", "frisanco", "udine", "pordenone", "trieste"],
    "Puglia": ["puglia", "bari", "ceglie", "japigia", "lecce", "foggia", "taranto"],
    "Campania": ["campania", "napoli", "salerno", "caserta"],
    "Lazio": ["lazio", "roma", "frosinone", "latina"],
    "Piemonte": ["piemonte", "torino", "alessandria"],
    "Liguria": ["liguria", "genova", "savona"],
    "Calabria": ["calabria", "reggio", "cosenza"],
    "Abruzzo": ["abruzzo", "pescara", "teramo", "chieti"],
    "Sardegna": ["sardegna", "cagliari"],
    "Marche": ["marche", "ancona", "pesaro"],
    "Molise": ["molise", "termoli", "campobasso"],
    "Umbria": ["umbria", "perugia"],
}

FOREIGN_LOCATION_HINTS = (
    "abu dhabi",
    "borouge",
    "petrolchimico borouge",
    "emirati arabi",
    "united arab emirates",
    "uae",
    "dubai",
    "qatar",
    "doha",
)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _looks_like_wildfire(title: str, description: str) -> bool:
    title_text = normalize_text(title)
    full_text = normalize_text(f"{title}\n{description}")
    title_hit = any(keyword in title_text for keyword in WILDFIRE_KEYWORDS)
    return title_hit or (any(keyword in full_text for keyword in WILDFIRE_KEYWORDS) and any(hint in full_text for hint in WILDFIRE_STRONG_HINTS))


def looks_like_wildfire_story(title: str, description: str = "") -> bool:
    return _looks_like_wildfire(title, description)


def _severity_for_wildfire(title: str, description: str) -> str:
    text = normalize_text(f"{title}\n{description}")
    if any(hint in text for hint in WILDFIRE_STRONG_HINTS):
        return "red"
    if any(keyword in text for keyword in WILDFIRE_KEYWORDS):
        return "orange"
    return "blue"


def is_probably_italian_wildfire_story(title: str, description: str = "", feed_url: str = "", region: str = "") -> bool:
    normalized = normalize_text(f"{title}\n{description}\n{region}")
    if any(token in normalized for token in FOREIGN_LOCATION_HINTS):
        return False

    if feed_url:
        for key, feeds in REGIONAL_RSS.items():
            if key != "nazionale" and feed_url in feeds:
                return True

    if "italia" in normalized or "italian" in normalized:
        return True

    return any(alias in normalized for aliases in REGION_ALIASES.values() for alias in aliases)


def _infer_region_label(text: str, feed_url: str) -> str:
    normalized = normalize_text(text)
    for label, aliases in REGION_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return label
    for key, label in REGION_LABELS.items():
        if key == "nazionale":
            continue
        candidate = key.replace("-", " ")
        if candidate in normalized:
            return label
    for key, feeds in REGIONAL_RSS.items():
        if feed_url in feeds:
            return REGION_LABELS.get(key, "Italia")
    return "Italia"


def _build_live_wildfire_event(title: str, description: str, link: str, published: str, feed_url: str) -> dict | None:
    if not _looks_like_wildfire(title, description):
        return None
    if not is_probably_italian_wildfire_story(title, description, feed_url=feed_url):
        return None

    region = _infer_region_label(f"{title}\n{description}", feed_url)
    lat, lon, geo_raw, _ = extract_coordinates(f"{title}\n{description}\n{region}", use_geocoder=False)
    if lat is None or lon is None:
        lat, lon, geo_raw, _ = extract_coordinates(region, use_geocoder=False)
    if lat is None or lon is None:
        lat, lon, geo_raw = 41.87, 12.57, region

    severity = _severity_for_wildfire(title, description)
    status_map = {"red": "CRITICO", "orange": "ATTENZIONE", "blue": "MODERATO"}
    started_at = parse_published_datetime(published)
    event_id = f"rss-wildfire-{canonical_url_hash(link)[:16]}"

    return {
        "id": event_id,
        "title": title[:240] or "Incendio segnalato da RSS locali",
        "type": EventCategory.wildfire.value,
        "category": EventCategory.wildfire.value,
        "severity": severity,
        "status": status_map.get(severity, "ATTENZIONE"),
        "lat": float(lat),
        "lon": float(lon),
        "region": region,
        "started_at": started_at,
        "updated_at": _utc_now_naive(),
        "is_alert": False,
        "derived_from": "rss_local_wildfire",
        "geo_raw": geo_raw,
    }


def get_live_wildfire_candidates(limit: int = 6) -> list[dict]:
    rows: list[dict] = []
    seen_ids: set[str] = set()
    feeds = REGIONAL_RSS.get("nazionale", []) + REGIONAL_RSS.get("lombardia", []) + REGIONAL_RSS.get("toscana", [])

    for feed_url in list(dict.fromkeys(feeds)):
        try:
            articles = parse_rss_feed(feed_url)[:25]
        except Exception as exc:
            logger.warning(f"[rss_local] live wildfire scan failed for {feed_url}: {exc}")
            continue

        for article in articles:
            title = (article.get("title") or "").strip()
            description = (article.get("description") or "").strip()
            link = normalize_absolute_url((article.get("link") or "").strip()) or ""
            published = (article.get("published") or "").strip()
            if not title or not link:
                continue
            normalized = normalize_text(f"{title}\n{description}")
            event = _build_live_wildfire_event(title, description, link, published, feed_url)
            if not event or event["id"] in seen_ids:
                continue
            seen_ids.add(event["id"])
            rows.append(event)
            if len(rows) >= limit:
                return rows

    return rows


def _ensure_wildfire_event(
    db: Session,
    title: str,
    description: str,
    link: str,
    published: str,
    feed_url: str,
) -> str | None:
    live_event = _build_live_wildfire_event(title, description, link, published, feed_url)
    if not live_event:
        return None

    event_id = str(live_event["id"])
    existing = db.query(Event).filter(Event.id == event_id).first()
    if existing is not None:
        existing.updated_at = _utc_now_naive()  # type: ignore[assignment]
        return str(existing.id)

    event = Event(
        id=event_id,
        title=str(live_event["title"]),
        type=str(live_event["type"]),
        category=str(live_event["category"]),
        severity=str(live_event["severity"]),
        status=str(live_event["status"]),
        lat=float(live_event["lat"]),
        lon=float(live_event["lon"]),
        region=str(live_event["region"]),
        started_at=live_event["started_at"],
        updated_at=live_event["updated_at"],
        is_alert=False,
        derived_from="rss_local_wildfire",
    )
    db.add(event)
    db.flush()
    return str(event.id)


def _content_hash(link: str) -> str:
    return canonical_url_hash(link)


def _source_id_from_feed(feed_url: str) -> str:
    return f"rss-local-{domain_name(feed_url)}"


def _upsert_source(db: Session, feed_url: str) -> str:
    src_id = _source_id_from_feed(feed_url)
    src = db.query(Source).filter(Source.id == src_id).first()
    if src is None:
        src = Source(
            id=src_id,
            name=domain_name(feed_url),
            type="notizie",
            platform="rss",
            url=feed_url,
            event_id=None,
            last_fetched=_utc_now_naive(),
            item_count=0,
        )
        db.add(src)
        db.flush()
    else:
        src.last_fetched = _utc_now_naive()  # type: ignore[assignment]
    return src_id


def _save_item(
    db: Session,
    event_id: str,
    source_id: str,
    link: str,
    title: str,
    description: str,
    published: str,
    confidence: float,
    relevance_score: float,
    thumb_url: Optional[str],
) -> bool:
    link = normalize_absolute_url(link) or ""
    if not link:
        return False
    thumb_url = normalize_absolute_url(thumb_url)
    source_name = source_id.replace("rss-local-", "")
    clean_title = clean_media_title(title, source_name=source_name, platform="rss")
    content_hash = _content_hash(link)
    existing = db.query(MediaItem).filter(MediaItem.content_hash == content_hash).first()
    if existing is not None:
        return False
    if is_semantic_duplicate(db, str(event_id), clean_title, media_type="article"):
        return False

    caption = f"{clean_title}\n{(description or '')[:300]}".strip()
    item = MediaItem(
        event_id=event_id,
        source_id=source_id,
        media_url=link,
        thumb_url=thumb_url,
        media_type="article",
        caption=caption,
        author=source_name,
        lat=None,
        lon=None,
        geo_raw=None,
        captured_at=parse_published_datetime(published),
        confidence=max(0, min(100, int(round(float(confidence or 0.0) * 100)))),
        relevance_score=round(float(relevance_score), 2),
        content_hash=content_hash,
    )

    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
    except IntegrityError:
        return False

    src = db.query(Source).filter(Source.id == source_id).first()
    if src is not None:
        src.item_count = int(src.item_count or 0) + 1  # type: ignore[assignment]
    return True


def fetch_rss_local(db: Session) -> int:
    """Collect regional/local Italian RSS news and link articles to active events."""
    try:
        events = db.query(Event).order_by(Event.updated_at.desc()).all()

        feed_cache: dict[str, list[dict]] = {}
        total_saved = 0
        og_calls = 0

        active_regions = {
            str(event.region or "").strip().lower().replace("_", "-")
            for event in events
            if str(event.region or "").strip()
        }
        feeds = []
        for region_key in sorted(active_regions):
            feeds.extend(REGIONAL_RSS.get(region_key, []))
        feeds.extend(REGIONAL_RSS.get("nazionale", []))
        feeds = list(dict.fromkeys(feeds))

        for feed_url in feeds:
            source_id = _upsert_source(db, feed_url)
            if feed_url not in feed_cache:
                feed_cache[feed_url] = parse_rss_feed(feed_url)
            articles = feed_cache[feed_url]

            saved_for_feed = 0
            for article in articles:
                title = (article.get("title") or "").strip()
                link = (article.get("link") or "").strip()
                description = (article.get("description") or "").strip()
                published = (article.get("published") or "").strip()
                link = normalize_absolute_url(link) or ""
                if not link or not title:
                    continue

                text = f"{title}\n{description}"
                art_dt = parse_published_datetime(published)
                matched_event_id, confidence = match_event(db, text, title)
                if not matched_event_id or confidence < 0.30:
                    wildfire_event_id = _ensure_wildfire_event(
                        db=db,
                        title=title,
                        description=description,
                        link=link,
                        published=published,
                        feed_url=feed_url,
                    )
                    if wildfire_event_id:
                        matched_event_id = wildfire_event_id
                        confidence = max(confidence, 0.72)
                if not matched_event_id or confidence < 0.30:
                    continue

                relevance_score = score_article_relevance(
                    title,
                    description,
                    link,
                    source_name=domain_name(feed_url),
                )
                if relevance_score < MIN_RELEVANCE_SCORE:
                    continue

                # Priority: RSS enclosure > description img > og:image (HTTP call)
                enclosure = normalize_absolute_url((article.get("enclosure_url") or "").strip())
                thumb_url: Optional[str] = enclosure if enclosure else None
                if not thumb_url:
                    thumb_url = normalize_absolute_url(extract_image_from_description(description))
                if not thumb_url and confidence >= 0.40:
                    if og_calls > 0:
                        time.sleep(0.3)
                    thumb_url = normalize_absolute_url(extract_og_image(link))
                    og_calls += 1

                saved = _save_item(
                    db=db,
                    event_id=str(matched_event_id),
                    source_id=source_id,
                    link=link,
                    title=title,
                    description=description,
                    published=published,
                    confidence=confidence,
                    relevance_score=relevance_score,
                    thumb_url=thumb_url,
                )
                if saved:
                    saved_for_feed += 1
                    total_saved += 1

            logger.info(
                f"[rss_local] {domain_name(feed_url)}: {len(articles)} articoli \u2192 {saved_for_feed} salvati"
            )

        return total_saved
    except Exception as exc:
        logger.warning(f"[rss_local] errore fetch: {exc}")
        return 0
